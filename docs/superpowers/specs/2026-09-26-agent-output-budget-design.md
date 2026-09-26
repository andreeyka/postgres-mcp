# Дизайн: бюджет ответа, формат вывода и схема тулов для агентов

Дата: 2026-09-26. Статус: дизайн согласован, спека ждёт ревью пользователя.

## 1. Контекст

В `netbox-mcp` (ветка `develop`, IA-10539) сделали две вещи для агентов:

- «умная» схема тулов: ввод агента нормализуется, а не отклоняется, и схема перечисляет все принимаемые формы;
- ограничение объёма ответа: слишком большой результат превращается в ошибку «уточните запрос».

В postgres-mcp сейчас:

- `execute_sql`, `list_objects`, `list_schemas` и `get_top_queries(sort_by="resources")` возвращают результат без ограничения объёма;
- `execute_sql` отдаёт список словарей, в котором имена колонок повторяются в каждой строке;
- лимит `analyze_query_indexes` в 10 запросов описан только в тексте, в JSON-схеме его нет;
- `object_type` и `health_type` принимают только одно написание;
- четыре ошибки уходят агенту по-русски.

Цель — удержать ответ в контексте агента и сократить число неудачных вызовов из-за формы ввода.

Работа идёт в ветке `claude/spec-auth-fastmcp4-hardening`, после шагов 1–2 спеки `2026-09-26-auth-fastmcp4-hardening-design.md`, и сдаётся одним PR вместе с ними. Ломающие изменения разрешены, легаси не поддерживается.

## 2. Решения

| Вопрос | Решение |
| --- | --- |
| Как ограничивать объём | Бюджет в токенах на весь ответ тула, а не лимит строк. Превышение даёт ошибку с подсказкой, как сузить запрос. `LIMIT`-обёртки и срез строк не делаем |
| Формат строк | Параметр `output: "table" \| "json"`, по умолчанию `table` |
| Схема | Границы прямо в JSON-схеме; нормализация ввода; английские ошибки с подсказкой; правила для авторов тулов и тесты на схему |

Вне объёма:

- ограничение нагрузки на БД и памяти процесса (их ограничивает только `statement_timeout`);
- постраничная выдача;
- изменение `explain_query` и `analyze_*`.

## 3. Бюджет ответа

Модуль `src/postgres_fastmcp/app/middleware/response_budget.py`.

```python
BYTES_PER_TOKEN = 3  # пессимистичная оценка: лимит срабатывает раньше, а не позже

def estimate_tokens(result: ToolResult) -> int: ...

class ResponseBudgetMiddleware(Middleware):
    def __init__(self, max_tokens: int) -> None: ...
    async def on_call_tool(self, context, call_next) -> ToolResult: ...
```

- Оценка равна сумме `len(text.encode())` по блокам `TextContent` в `result.content`, делённой на `BYTES_PER_TOKEN` с округлением вверх. `structured_content` не учитывается: в режиме `json` это дубль текста.
- Если оценка больше `max_tokens`, middleware бросает `ResponseTooLargeError(tokens, max_tokens)` вместо того, чтобы вернуть результат.
- Конфиг: `ServerSettings.response_max_tokens: int`, по умолчанию `20000`, ограничение `ge=1000`, переменная окружения `MCP_RESPONSE_MAX_TOKENS`.
- В `create_server` middleware стоит первым в списке, то есть внешним. Он проверяет ровно то, что уходит клиенту, включая результат `extra_middleware`. `LoggingMiddleware` при отказе запишет успешный внутренний вызов, это допустимо.

Текст ошибки (класс в `shared/errors.py`, наследник `UserFacingError`):

```text
Response is too large: ~{tokens} tokens, the limit is {max_tokens}. Refine the request:
add WHERE or LIMIT, select only the needed columns, aggregate (count, group by),
or narrow the schema/object filter.
```

## 4. Формат `output`

Модуль `src/postgres_fastmcp/tools/rendering.py`. От регистрации тулов он не зависит: шаг 3 основной спеки перестраивает тулы в `ToolSet`/`PostgresProvider`, и рендер должен пережить эту перестройку без изменений.

```python
OutputFormat = Literal["table", "json"]

def rows_result(rows: list[dict[str, Any]], output: OutputFormat, *, title: str | None = None) -> ToolResult: ...
def sections_result(sections: Mapping[str, list[dict[str, Any]]], output: OutputFormat, *, header: Mapping[str, Any] | None = None) -> ToolResult: ...
```

`table`:

- GFM-таблица, в которой колонки перечислены один раз;
- в ячейках `|` экранируется, перевод строки заменяется на пробел, `None` выводится пустой ячейкой;
- после таблицы идёт строка `{N} rows.`, а для пустого результата — `0 rows.` без таблицы;
- `sections_result` выводит по разделу `### name` с таблицей на каждый непустой раздел; скалярные поля заголовка идут строками `key: value`.

`json`:

- `ToolResult(content=[TextContent(json.dumps(data, default=str))], structured_content=data)`;
- для строк `data = {"rows": [...], "row_count": N}`, для разделов — словарь разделов.
- Markdown и JSON в одном ответе не дублируются никогда.

Тулы, которые получают `output`:

| Тул | Результат |
| --- | --- |
| `execute_sql` | `rows_result`; для оператора без результата — `0 rows.` / `{"rows": [], "row_count": 0}` |
| `list_schemas` | `rows_result` |
| `list_objects` | `rows_result` |
| `get_top_queries` | `rows_result` для всех `sort_by`; сейчас тул отдаёт `str(list)` |
| `get_object_details` | `sections_result`: заголовок (schema, name, type), списковые поля результата сервиса как разделы (набор зависит от типа объекта), скалярные — в заголовок |

Эти тулы регистрируются с `output_schema=None`: иначе FastMCP заворачивает ответ в `{"result": ...}`.

## 5. Параметры и границы

Модуль `src/postgres_fastmcp/tools/params.py` с общими `Annotated`-типами. Это обычные присваивания, не `type X = ...`: у PEP 695-алиаса docstring функции перебивает описание `Field`.

| Параметр | Принимает | Нормализует в | Отклоняет |
| --- | --- | --- | --- |
| `object_type` | `table`, `Tables`, `TABLE`, `views`, `sequence(s)`, `extension(s)` | каноническое значение `ObjectType` | прочее — `UnsupportedObjectTypeError` с подсказкой `Did you mean '...'?` (difflib) и списком допустимых |
| `health_type` | список или CSV-строку, регистр любой, `all` | список значений `HealthType` (`all` поглощает остальные) | неизвестное значение — с подсказкой и списком допустимых |
| `sort_by` (`get_top_queries`) | текущие значения и синонимы: `total`→`total_time`, `mean`/`avg`→`mean_time`, `resource`→`resources` | значение `TopQueriesSortBy` | прочее — `InvalidSortCriteriaError` с подсказкой |
| `limit` (`get_top_queries`) | целое ≥ 1 | `min(limit, TOP_QUERIES_MAX_LIMIT)`, где `TOP_QUERIES_MAX_LIMIT = 100` | < 1 |
| `queries` (`analyze_query_indexes`) | список из 1–`MAX_NUM_INDEX_TUNING_QUERIES` строк | — | пустой или длиннее — схема (`min_length`/`max_length`) |
| `output` | `table`, `json`, регистр любой | нижний регистр | прочее |

- Где принимаемая форма шире базового типа, JSON-схема описывает её через `WithJsonSchema` (например, `anyOf` строка/массив для `health_type`). Так клиент не отклонит ввод раньше сервера.
- `limit` начинает действовать и для `sort_by="resources"`: сервис получает параметр `limit` и добавляет в SQL `LIMIT`. Сейчас эта ветка отдаёт все строки выше порога.
- Описания `Field(description=...)` на английском. Они перечисляют принимаемые формы и дают пример.

## 6. Ошибки

- Четыре класса в `shared/errors.py`, которые сейчас пишут агенту по-русски, переводятся на английский: `ExplainAnalyzeWithHypotheticalError`, `EmptyQueriesError`, `QueriesLimitError`, `InvalidSortCriteriaError`.
- У каждой ошибки тула, которую агент может исправить сам, в конце есть подсказка, что сделать вместо этого.
- Правило из памяти проекта: по-русски пишутся только комментарии и docstring, которые не попадают к агенту или во внешнюю систему.

## 7. Правила для авторов тулов

Файл `src/postgres_fastmcp/tools/AGENTS.md`, рядом `CLAUDE.md` со строкой `@AGENTS.md`. Содержание:

- всё, что видит агент (описания тулов, `Field(description=...)`, docstring функций тулов, ошибки), пишется на английском;
- ограничения задаются через `Annotated[Type, Field(...)]`; общие алиасы — обычные присваивания; на DI-параметрах (`ctx`) `Field` не ставится;
- ввод нормализуется, а не отклоняется; схема не строже того, что сервер реально принимает;
- тулы, которые возвращают строки, принимают `output` и возвращают `ToolResult` через `tools/rendering.py`, с `output_schema=None`;
- ошибки наследуют `UserFacingError`, иначе их текст скроет `mask_error_details=True`.

Корневой `AGENTS.md` получает ссылку на этот файл.

## 8. Тестирование

Unit:

- `estimate_tokens` и `ResponseBudgetMiddleware`: маленький ответ проходит, большой даёт `ResponseTooLargeError` с числами, `structured_content` не учитывается;
- интеграция middleware в `create_server` через `fastmcp.Client` в памяти с моком БД: большой результат `execute_sql` доходит до клиента ошибкой с текстом `Refine the request`;
- `rendering`: экранирование `|`, переносы строк, `None`, пустой результат, `json` без Markdown, `sections_result`;
- `params`: каждая строка таблицы раздела 5 (нормализация и отказ с подсказкой);
- схема: описания `Field` доходят до `inputSchema` каждого тула; у `queries` есть `maxItems`; у `health_type` есть `anyOf`;
- бюджет схем: длина JSON всего `tools/list` в режиме FULL не больше порога. Порог равен текущему размеру после изменений плюс 15 % и фиксируется числом в тесте;
- язык: сообщения всех подклассов `UserFacingError`, созданных с тестовыми аргументами, не содержат кириллицы.

Существующие тесты тулов адаптируются к `ToolResult`. Интеграционные тесты, которые разбирают ответ `execute_sql` и других тулов, переходят на `output="json"` и `structured_content`.

## 9. Ломающие изменения

- Тулы `execute_sql`, `list_schemas`, `list_objects`, `get_top_queries`, `get_object_details` по умолчанию возвращают Markdown-таблицу. Прежняя форма данных доступна через `output="json"`, но уже внутри `{"rows": ..., "row_count": ...}`.
- Ответ больше `response_max_tokens` заменяется ошибкой.
- `get_top_queries(sort_by="resources")` учитывает `limit`.
- Тексты четырёх ошибок изменились.
