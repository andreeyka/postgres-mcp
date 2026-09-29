"""Запросы каталога для проверки выражений плана: SQL сервера в транзакции оператора агента.

Идут через исполнитель транзакции PlanGuard (search_path = allowed_schema, SET LOCAL уже выставлен), без
валидатора агента: это SQL сервера. Имена встраиваются как Literal. Каждое отношение, функция и тип — с
pg_catalog., каждый оператор — OPERATOR(pg_catalog.…): pg_catalog неявно первый в search_path, но
неквалифицированный оператор с разными типами аргументов (oid <> integer) public может перехватить
точным совпадением типов. IN, NULLIF и IS DISTINCT FROM ищут = так же — вместо них ANY с OPERATOR.
"""

from collections.abc import Collection

from psycopg.sql import SQL, Composable, Literal

from postgres_fastmcp.postgres.models import RowResult
from postgres_fastmcp.postgres.ports import StatementRunner


_BUILTIN_TYPES_SQL = (
    "SELECT t.typname AS name FROM pg_catalog.pg_type t "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) t.typnamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) 'pg_catalog'"
)
_PG_CATALOG_FUNCTIONS_SQL = (
    "SELECT DISTINCT p.proname AS name FROM pg_catalog.pg_proc p "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) p.pronamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) 'pg_catalog' AND p.proname OPERATOR(pg_catalog.=) ANY ({names})"
)
# Строковый тип отношения — сам (typrelid), через домен (typbasetype; у домена typrelid = 0, домен над
# доменом ссылается на ближайший) или через массив (typelem): '{"(1,2)"}'::users_dom раскрывает таблицу так же.
_ROW_TYPES_SQL = (
    "WITH RECURSIVE reached(name, oid) AS ("
    "SELECT t.typname, t.oid FROM pg_catalog.pg_type t "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) t.typnamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND t.typname OPERATOR(pg_catalog.=) ANY ({names}) "
    "UNION "
    "SELECT r.name, u.next FROM reached r "
    "JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) r.oid "
    "CROSS JOIN LATERAL (VALUES (t.typbasetype), (t.typelem)) AS u(next) "
    "WHERE u.next OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"
    ") "
    "SELECT r.name, cn.nspname AS relation_schema, c.relname AS relation_name FROM reached r "
    "JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) r.oid "
    "JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) t.typrelid "
    "JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace"
)

# Опорные функции агрегата (pg_aggregate, PG 15–17): переход, финал, комбинирование, (де)сериализация и их
# варианты для движущегося окна; regproc, 0 — функции нет (соединение с pg_proc такую строку отбрасывает).
_AGGREGATE_SUPPORT_FUNCTIONS = (
    "(a.aggtransfn), (a.aggfinalfn), (a.aggcombinefn), (a.aggserialfn), "
    "(a.aggdeserialfn), (a.aggmtransfn), (a.aggminvtransfn), (a.aggmfinalfn)"
)

# Определения, до которых дошёл разбор запросов агента (PREPARE) и планировщик (EXPLAIN): всё, что эта транзакция
# заблокировала (pg_locks — и fast-path блокировки), кроме pg_catalog и pg_toast (их блокируют и собственные
# запросы каталога).
#
# Правила (pg_rewrite). Материализованные представления (relkind = 'm') пропускаются: их данные уже скопированы,
# чтение не вычисляет определение. Правило не ON SELECT (ev_type <> '1', оно есть только у DML-таблиц) берётся,
# только если транзакция держит на отношении блокировку DML (RowExclusiveLock и строже): только тогда оно может
# сработать. Правило ON SELECT берётся всегда: чтение представления выполняет его "_RETURN".
#
# Определения отношений. Планировщик сворачивает IMMUTABLE-вызовы с константами (выполняет их при EXPLAIN)
# и для простого SELECT: CHECK потомков наследования (исключение по ограничениям), выражения и предикаты индексов
# (сопоставление с условиями), ключи секционирования (их строит кэш отношения), выражения расширенной статистики
# (pg_statistic_ext.stxexprs), политики RLS. Поэтому CHECK, индексы, ключи секционирования и статистика читаются
# у каждого заблокированного отношения, каждой цели DML (ниже) и всех их потомков (pg_inherits, рекурсивно:
# потомков SELECT блокирует только планировщик, после PREPARE их ещё нет в pg_locks), а политики — все политики
# (независимо от команды и роли) каждого заблокированного отношения и цели DML с включённым RLS. У ключа
# секционирования зависимости pg_depend записаны на саму таблицу с objsubid = 0 (с номером колонки там типы
# колонок — их не берём).
#
# Путь записи. Цели — отношения с блокировкой DML, их потомки (pg_inherits: PREPARE блокирует только названную
# секционированную таблицу, а триггеры и ограничения секций срабатывают при маршрутизации) и таблицы, которые
# ссылаются на цель внешним ключом с каскадом (CASCADE, SET NULL, SET DEFAULT меняют ссылающуюся таблицу), —
# рекурсивно. Их объекты: включённые пользовательские триггеры, умолчания и генерируемые колонки (pg_attrdef),
# CHECK и умолчания (typdefaultbin: переписчик подставляет его, если у колонки умолчания нет) доменов колонок
# (рекурсивно по базовым доменам и элементам массивов; NOT NULL домена без conbin пропускается). Для SELECT
# они не вычисляются (проверено на PG 17: умолчания и CHECK доменов колонок при чтении не сворачиваются).
# Атрибуты составных типов колонок не обходятся: рекурсия по pg_attribute поднимает оценку запроса выше
# jit_above_cost (100000; живьём 107795 против 15812 без неё — замер до развёртывания множеств через unnest,
# см. _oid_set), и JIT компилировал бы каждый запрос проверки.
#
# Строки (kind): rule, trigger, check, domain, default, index, partition, statistics, policy — текст определения
# (definition; имена вне search_path — со схемой); function — функция или агрегат из pg_depend этих объектов;
# aggregate_function — опорная функция агрегата (parent_schema — схема агрегата); operator и operator_function —
# оператор и его функция (oprcode); type — тип и каждый тип, до которого он ведёт через typbasetype/typelem, с
# отношением строкового типа (relation_*). pg_depend не хранит зависимостей от закреплённых (встроенных) объектов
# pg_catalog: они видны только в тексте.
_PG_REWRITE = "'pg_catalog.pg_rewrite'::pg_catalog.regclass::pg_catalog.oid"
_PG_PROC = "'pg_catalog.pg_proc'::pg_catalog.regclass::pg_catalog.oid"
_PG_OPERATOR = "'pg_catalog.pg_operator'::pg_catalog.regclass::pg_catalog.oid"
_PG_TYPE = "'pg_catalog.pg_type'::pg_catalog.regclass::pg_catalog.oid"
_NO_PARENT = "NULL::pg_catalog.name"
_NO_RELATION = "NULL::pg_catalog.name, NULL::pg_catalog.name"
_NO_DEFINITION = "NULL::pg_catalog.text"
# Режимы блокировки DML (RowExclusiveLock и выше): их берут операторы, меняющие отношение, а не только
# читающие его (AccessShareLock — SELECT, RowShareLock — SELECT FOR SHARE/UPDATE его не берут).
_DML_LOCK_MODES = (
    "ARRAY['RowExclusiveLock', 'ShareUpdateExclusiveLock', 'ShareLock', 'ShareRowExclusiveLock', "
    "'ExclusiveLock', 'AccessExclusiveLock']::pg_catalog.text[]"
)
_PG_TRIGGER = "'pg_catalog.pg_trigger'::pg_catalog.regclass::pg_catalog.oid"
_PG_CONSTRAINT = "'pg_catalog.pg_constraint'::pg_catalog.regclass::pg_catalog.oid"
_PG_ATTRDEF = "'pg_catalog.pg_attrdef'::pg_catalog.regclass::pg_catalog.oid"
_PG_CLASS = "'pg_catalog.pg_class'::pg_catalog.regclass::pg_catalog.oid"
_PG_POLICY = "'pg_catalog.pg_policy'::pg_catalog.regclass::pg_catalog.oid"
_PG_STATISTIC_EXT = "'pg_catalog.pg_statistic_ext'::pg_catalog.regclass::pg_catalog.oid"
# Зависимости объекта с любым objsubid (у колонок таблицы objsubid — номер колонки: их типы не нужны).
_ANY_SUBID = "NULL::pg_catalog.int4"
_NO_NAME = "NULL::pg_catalog.name"
# Действия внешнего ключа, которые меняют ссылающуюся таблицу: CASCADE, SET NULL, SET DEFAULT.
_CASCADE_ACTIONS = "ARRAY['c', 'n', 'd']::pg_catalog.\"char\"[]"
# Живая пользовательская колонка pg_attribute (не системная, не удалённая).
_LIVE_COLUMN = "a.attnum OPERATOR(pg_catalog.>) 0::pg_catalog.int2 AND NOT a.attisdropped"


def _oid_set(name: str, alias: str) -> str:
    """Строки множества oid из массива CTE name (oids) под псевдонимом alias.

    Рекурсивный CTE планировщик оценивает с запасом (рабочая таблица — в десять раз больше начальной), и через
    соединения с каталогом оценка росла бы до миллионов строк (выше jit_above_cost). Массив, развёрнутый
    unnest, оценивается в десять строк.
    """
    return f"{name} {alias}_set CROSS JOIN pg_catalog.unnest({alias}_set.oids) AS {alias}(oid)"


_TARGETS = _oid_set("target_set", "t")
_RELATIONS = _oid_set("relation_set", "t")
_COLUMN_TYPES = _oid_set("type_set", "y")


def _definition_rows(kind: str, text: str, source: str) -> str:
    """Строки текста определения вида kind: text — выражение текста над source."""
    return f"SELECT '{kind}', {_NO_NAME}, {_NO_NAME}, {_NO_PARENT}, {_NO_RELATION}, {text} FROM {source} "  # noqa: S608


# Подстановки — константы модуля выше, ввода агента в тексте нет.
DEFINITION_DEPENDENCIES_SQL = (
    "WITH RECURSIVE locked AS ("  # noqa: S608
    "SELECT DISTINCT c.oid, c.relkind, l.mode FROM pg_catalog.pg_locks l "
    "JOIN pg_catalog.pg_database db ON db.oid OPERATOR(pg_catalog.=) l.database "
    "JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) l.relation "
    "JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace "
    "WHERE l.locktype OPERATOR(pg_catalog.=) 'relation' "
    "AND l.pid OPERATOR(pg_catalog.=) pg_catalog.pg_backend_pid() "
    "AND db.datname OPERATOR(pg_catalog.=) pg_catalog.current_database() "
    "AND cn.nspname OPERATOR(pg_catalog.<>) ALL (ARRAY['pg_catalog', 'pg_toast']::pg_catalog.name[])"
    "), rules AS ("
    "SELECT DISTINCT r.oid FROM locked k JOIN pg_catalog.pg_rewrite r ON r.ev_class OPERATOR(pg_catalog.=) k.oid "
    "WHERE k.relkind OPERATOR(pg_catalog.<>) 'm' "
    f"AND (r.ev_type OPERATOR(pg_catalog.=) '1' OR k.mode OPERATOR(pg_catalog.=) ANY ({_DML_LOCK_MODES}))"
    "), targets(oid) AS ("
    "SELECT k.oid FROM pg_catalog.unnest(ARRAY("
    f"SELECT k.oid FROM locked k WHERE k.mode OPERATOR(pg_catalog.=) ANY ({_DML_LOCK_MODES})"
    ")) AS k(oid) "
    "UNION "
    "SELECT n.oid FROM targets t JOIN ("
    "SELECT i.inhparent, i.inhrelid FROM pg_catalog.pg_inherits i "
    "UNION ALL "
    "SELECT f.confrelid, f.conrelid FROM pg_catalog.pg_constraint f WHERE f.contype OPERATOR(pg_catalog.=) 'f' "
    f"AND (f.confdeltype OPERATOR(pg_catalog.=) ANY ({_CASCADE_ACTIONS}) "
    f"OR f.confupdtype OPERATOR(pg_catalog.=) ANY ({_CASCADE_ACTIONS}))"
    ") AS n(parent, oid) ON n.parent OPERATOR(pg_catalog.=) t.oid"
    "), target_set(oids) AS ("
    "SELECT ARRAY(SELECT t.oid FROM targets t)"
    "), relations(oid) AS ("
    f"SELECT k.oid FROM locked k UNION SELECT t.oid FROM {_TARGETS} "
    "UNION "
    "SELECT i.inhrelid FROM relations r "
    "JOIN pg_catalog.pg_inherits i ON i.inhparent OPERATOR(pg_catalog.=) r.oid"
    "), relation_set(oids) AS ("
    "SELECT ARRAY(SELECT r.oid FROM relations r)"
    "), column_types(oid) AS ("
    f"SELECT a.atttypid FROM {_TARGETS} "
    f"JOIN pg_catalog.pg_attribute a ON a.attrelid OPERATOR(pg_catalog.=) t.oid WHERE {_LIVE_COLUMN} "
    "UNION "
    "SELECT n.oid FROM column_types y JOIN pg_catalog.pg_type ty ON ty.oid OPERATOR(pg_catalog.=) y.oid "
    "CROSS JOIN LATERAL (VALUES (ty.typbasetype), (ty.typelem)) AS n(oid) "
    "WHERE n.oid OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"
    "), type_set(oids) AS ("
    "SELECT ARRAY(SELECT y.oid FROM column_types y)"
    "), triggers AS ("
    f"SELECT g.oid FROM {_TARGETS} JOIN pg_catalog.pg_trigger g ON g.tgrelid OPERATOR(pg_catalog.=) t.oid "
    "WHERE NOT g.tgisinternal AND g.tgenabled OPERATOR(pg_catalog.<>) 'D'"
    "), checks AS ("
    f"SELECT k.oid, k.conbin, k.conrelid FROM {_RELATIONS} "
    "JOIN pg_catalog.pg_constraint k ON k.conrelid OPERATOR(pg_catalog.=) t.oid "
    "WHERE k.contype OPERATOR(pg_catalog.=) 'c'"
    "), domain_checks AS ("
    f"SELECT k.oid, k.conbin FROM {_COLUMN_TYPES} "
    "JOIN pg_catalog.pg_constraint k ON k.contypid OPERATOR(pg_catalog.=) y.oid WHERE k.conbin IS NOT NULL"
    "), domain_defaults AS ("
    f"SELECT ty.oid, ty.typdefaultbin FROM {_COLUMN_TYPES} "
    "JOIN pg_catalog.pg_type ty ON ty.oid OPERATOR(pg_catalog.=) y.oid WHERE ty.typdefaultbin IS NOT NULL"
    "), defaults AS ("
    f"SELECT d.oid, d.adbin, d.adrelid FROM {_TARGETS} "
    "JOIN pg_catalog.pg_attrdef d ON d.adrelid OPERATOR(pg_catalog.=) t.oid"
    "), indexes AS ("
    f"SELECT x.indexrelid AS oid FROM {_RELATIONS} "
    "JOIN pg_catalog.pg_index x ON x.indrelid OPERATOR(pg_catalog.=) t.oid "
    "WHERE x.indexprs IS NOT NULL OR x.indpred IS NOT NULL"
    "), partition_keys AS ("
    f"SELECT c.oid FROM {_RELATIONS} JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) t.oid "
    "WHERE c.relkind OPERATOR(pg_catalog.=) 'p'"
    "), stats AS ("
    f"SELECT s.oid FROM {_RELATIONS} "
    "JOIN pg_catalog.pg_statistic_ext s ON s.stxrelid OPERATOR(pg_catalog.=) t.oid WHERE s.stxexprs IS NOT NULL"
    "), policies AS ("
    "SELECT p.oid, p.polrelid, p.polqual, p.polwithcheck "
    f"FROM (SELECT k.oid FROM locked k UNION SELECT t.oid FROM {_TARGETS}) AS t(oid) "
    "JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) t.oid "
    "JOIN pg_catalog.pg_policy p ON p.polrelid OPERATOR(pg_catalog.=) t.oid WHERE c.relrowsecurity"
    "), objects(classid, objid, objsubid) AS ("
    f"SELECT {_PG_REWRITE}, u.oid, {_ANY_SUBID} FROM rules u UNION ALL "
    f"SELECT {_PG_TRIGGER}, g.oid, {_ANY_SUBID} FROM triggers g UNION ALL "
    f"SELECT {_PG_CONSTRAINT}, k.oid, {_ANY_SUBID} FROM checks k UNION ALL "
    f"SELECT {_PG_CONSTRAINT}, k.oid, {_ANY_SUBID} FROM domain_checks k UNION ALL "
    f"SELECT {_PG_TYPE}, ty.oid, {_ANY_SUBID} FROM domain_defaults ty UNION ALL "
    f"SELECT {_PG_ATTRDEF}, d.oid, {_ANY_SUBID} FROM defaults d UNION ALL "
    f"SELECT {_PG_CLASS}, x.oid, {_ANY_SUBID} FROM indexes x UNION ALL "
    f"SELECT {_PG_CLASS}, c.oid, 0::pg_catalog.int4 FROM partition_keys c UNION ALL "
    f"SELECT {_PG_STATISTIC_EXT}, s.oid, {_ANY_SUBID} FROM stats s UNION ALL "
    f"SELECT {_PG_POLICY}, p.oid, {_ANY_SUBID} FROM policies p"
    "), dependencies AS ("
    "SELECT DISTINCT d.refclassid, d.refobjid FROM objects o "
    "JOIN pg_catalog.pg_depend d ON d.classid OPERATOR(pg_catalog.=) o.classid "
    "AND d.objid OPERATOR(pg_catalog.=) o.objid "
    "AND (o.objsubid IS NULL OR d.objsubid OPERATOR(pg_catalog.=) o.objsubid)"
    "), types(oid) AS ("
    f"SELECT x.refobjid FROM dependencies x WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_TYPE} "
    "UNION "
    "SELECT v.next FROM types y JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) y.oid "
    "CROSS JOIN LATERAL (VALUES (t.typbasetype), (t.typelem)) AS v(next) "
    "WHERE v.next OPERATOR(pg_catalog.<>) 0::pg_catalog.oid"
    ") "
    f"SELECT 'rule' AS kind, {_NO_NAME} AS schema, {_NO_NAME} AS name, {_NO_PARENT} AS parent_schema, "
    f"{_NO_NAME} AS relation_schema, {_NO_NAME} AS relation_name, "
    "pg_catalog.pg_get_ruledef(u.oid) AS definition FROM rules u "
    "UNION ALL "
    + _definition_rows("trigger", "pg_catalog.pg_get_triggerdef(g.oid)", "triggers g")
    + "UNION ALL "
    + _definition_rows("check", "pg_catalog.pg_get_expr(k.conbin, k.conrelid)", "checks k")
    + "UNION ALL "
    + _definition_rows("domain", "pg_catalog.pg_get_expr(k.conbin, 0::pg_catalog.oid)", "domain_checks k")
    + "UNION ALL "
    + _definition_rows("default", "pg_catalog.pg_get_expr(d.adbin, d.adrelid)", "defaults d")
    + "UNION ALL "
    + _definition_rows("default", "pg_catalog.pg_get_expr(ty.typdefaultbin, 0::pg_catalog.oid)", "domain_defaults ty")
    + "UNION ALL "
    + _definition_rows("index", "pg_catalog.pg_get_indexdef(x.oid)", "indexes x")
    + "UNION ALL "
    + _definition_rows("partition", "pg_catalog.pg_get_partkeydef(c.oid)", "partition_keys c")
    + "UNION ALL "
    + _definition_rows(
        "statistics",
        "e.expr",
        "stats s CROSS JOIN LATERAL "
        "pg_catalog.unnest(pg_catalog.pg_get_statisticsobjdef_expressions(s.oid)) AS e(expr)",
    )
    + "UNION ALL "
    + _definition_rows(
        "policy",
        "pg_catalog.pg_get_expr(e.expr, p.polrelid)",
        "policies p CROSS JOIN LATERAL (VALUES (p.polqual), (p.polwithcheck)) AS e(expr) WHERE e.expr IS NOT NULL",
    )
    + "UNION ALL "  # noqa: S608
    f"SELECT 'function', pn.nspname, p.proname, {_NO_PARENT}, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x JOIN pg_catalog.pg_proc p ON p.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace pn ON pn.oid OPERATOR(pg_catalog.=) p.pronamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_PROC} "
    "UNION ALL "
    f"SELECT 'aggregate_function', fn.nspname, f.proname, an.nspname, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x "
    "JOIN pg_catalog.pg_aggregate a ON a.aggfnoid::pg_catalog.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_proc ap ON ap.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace an ON an.oid OPERATOR(pg_catalog.=) ap.pronamespace "
    f"CROSS JOIN LATERAL (VALUES {_AGGREGATE_SUPPORT_FUNCTIONS}) AS s(fn) "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) s.fn::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_PROC} "
    "UNION ALL "
    f"SELECT 'operator', opn.nspname, o.oprname, {_NO_PARENT}, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_OPERATOR} "
    "UNION ALL "
    f"SELECT 'operator_function', fn.nspname, f.proname, opn.nspname, {_NO_RELATION}, {_NO_DEFINITION} "
    "FROM dependencies x JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) x.refobjid "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) o.oprcode::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    f"WHERE x.refclassid OPERATOR(pg_catalog.=) {_PG_OPERATOR} "
    "UNION ALL "
    f"SELECT 'type', tn.nspname, t.typname, {_NO_PARENT}, cn.nspname, c.relname, {_NO_DEFINITION} "
    "FROM types y JOIN pg_catalog.pg_type t ON t.oid OPERATOR(pg_catalog.=) y.oid "
    "JOIN pg_catalog.pg_namespace tn ON tn.oid OPERATOR(pg_catalog.=) t.typnamespace "
    "LEFT JOIN pg_catalog.pg_class c ON c.oid OPERATOR(pg_catalog.=) t.typrelid "
    "LEFT JOIN pg_catalog.pg_namespace cn ON cn.oid OPERATOR(pg_catalog.=) c.relnamespace"
)

# Реализации операторов и агрегатов allowed_schema по именам. План и SQL агента печатают оператор или агрегат
# allowed_schema без схемы и без типов аргументов, поэтому берутся все перегрузки с этим именем. Строки — того же
# вида, что у DEFINITION_DEPENDENCIES_SQL (их проверяет тот же разбор): operator_function — функция оператора (oprcode);
# aggregate_function — опорная функция агрегата; operator и operator_function — оператор сортировки агрегата
# (aggsortop: min/max планировщик заменяет индексным сканом с этим оператором) и его функция. parent_schema —
# схема оператора или агрегата.
ALLOWED_IMPLEMENTATIONS_SQL = (
    "WITH operators AS ("  # noqa: S608
    "SELECT o.oprcode FROM pg_catalog.pg_operator o "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND o.oprname OPERATOR(pg_catalog.=) ANY ({operators})"
    "), aggregates AS ("
    "SELECT a.* FROM pg_catalog.pg_aggregate a "
    "JOIN pg_catalog.pg_proc p ON p.oid OPERATOR(pg_catalog.=) a.aggfnoid::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace n ON n.oid OPERATOR(pg_catalog.=) p.pronamespace "
    "WHERE n.nspname OPERATOR(pg_catalog.=) {schema} AND p.proname OPERATOR(pg_catalog.=) ANY ({functions})"
    "), sort_operators AS ("
    "SELECT o.oprname, o.oprnamespace, o.oprcode FROM aggregates a "
    "JOIN pg_catalog.pg_operator o ON o.oid OPERATOR(pg_catalog.=) a.aggsortop"
    ") "
    "SELECT 'operator_function' AS kind, fn.nspname AS schema, f.proname AS name, "
    "{schema}::pg_catalog.name AS parent_schema "
    "FROM operators o JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) o.oprcode::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    "UNION ALL "
    "SELECT 'aggregate_function', fn.nspname, f.proname, {schema}::pg_catalog.name "
    f"FROM aggregates a CROSS JOIN LATERAL (VALUES {_AGGREGATE_SUPPORT_FUNCTIONS}) AS s(fn) "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) s.fn::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace "
    "UNION ALL "
    "SELECT 'operator', opn.nspname, o.oprname, NULL::pg_catalog.name FROM sort_operators o "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "UNION ALL "
    "SELECT 'operator_function', fn.nspname, f.proname, opn.nspname FROM sort_operators o "
    "JOIN pg_catalog.pg_namespace opn ON opn.oid OPERATOR(pg_catalog.=) o.oprnamespace "
    "JOIN pg_catalog.pg_proc f ON f.oid OPERATOR(pg_catalog.=) o.oprcode::pg_catalog.oid "
    "JOIN pg_catalog.pg_namespace fn ON fn.oid OPERATOR(pg_catalog.=) f.pronamespace"
)


def _names(rows: list[RowResult] | None) -> frozenset[str]:
    """Значения колонки name."""
    return frozenset(str(row.cells["name"]) for row in rows or ())


def _name_array(names: Collection[str]) -> Composable:
    """ARRAY[...]::pg_catalog.name[] из Literal для OPERATOR(pg_catalog.=) ANY (...)."""
    return SQL("ARRAY[{}]::pg_catalog.name[]").format(SQL(", ").join(Literal(name) for name in names))


class BuiltinTypeNames:
    """Имена типов pg_catalog: один запрос на время жизни объекта (DbAccessService держит один на пул).

    Имя без схемы, которое есть в pg_catalog, означает тип pg_catalog (он первый в search_path). Тип,
    появившийся в pg_catalog после загрузки, лишь даёт лишний запрос строковых типов. Тип, удалённый из
    pg_catalog после загрузки (DROP EXTENSION, установленного в pg_catalog), остаётся в кэше до перезапуска:
    одноимённая таблица public без префикса, названная без схемы, пропустит проверку строкового типа.
    Риск пренебрежимый и принят сознательно. Функции так не кэшируются: пропущенная функция pg_catalog
    считалась бы функцией allowed_schema.
    """

    def __init__(self) -> None:
        """Пустой кэш: загрузка при первой проверке, где нужен."""
        self._names: frozenset[str] | None = None

    async def load(self, run: StatementRunner) -> frozenset[str]:
        """Имена типов pg_catalog; запрос — только при первом вызове."""
        if self._names is None:
            self._names = _names(await run(_BUILTIN_TYPES_SQL))
        return self._names


async def pg_catalog_functions(run: StatementRunner, names: Collection[str]) -> frozenset[str]:
    """Какие из имён — функции pg_catalog (одним запросом)."""
    sql = SQL(_PG_CATALOG_FUNCTIONS_SQL).format(names=_name_array(names)).as_string()
    return _names(await run(sql))


async def row_types(run: StatementRunner, schema: str, names: Collection[str]) -> dict[str, list[tuple[str, str]]]:
    """Какие из имён типов схемы schema ведут к строковому типу отношения — и к какому (схема, имя).

    Отношение — таблица, представление или составной тип; путь — сам тип, домен над ним или массив.
    """
    sql = SQL(_ROW_TYPES_SQL).format(schema=Literal(schema), names=_name_array(names)).as_string()
    found: dict[str, list[tuple[str, str]]] = {}
    for row in await run(sql) or ():
        relation = (str(row.cells["relation_schema"]), str(row.cells["relation_name"]))
        found.setdefault(str(row.cells["name"]), []).append(relation)
    return found


async def allowed_implementations(
    run: StatementRunner, schema: str, *, operators: Collection[str], functions: Collection[str]
) -> list[RowResult] | None:
    """Функции, которыми реализованы операторы и агрегаты схемы schema с этими именами (одним запросом)."""
    sql = (
        SQL(ALLOWED_IMPLEMENTATIONS_SQL)
        .format(schema=Literal(schema), operators=_name_array(operators), functions=_name_array(functions))
        .as_string()
    )
    return await run(sql)
