# mypy: ignore-errors
#!/usr/bin/env python3
"""Script to prepare Docker images for testing before running tests.

This script builds the required Docker images so that tests can run faster.
"""

from __future__ import annotations

import logging
import sys

import docker
from docker import errors as docker_errors


logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def build_postgres_image(version: str) -> bool:
    """Build PostgreSQL image with HypoPG for testing.

    Args:
        version: PostgreSQL version (e.g., "15" or "16")

    Returns:
        True if successful, False otherwise
    """
    try:
        client = docker.from_env()
        client.ping()
    except (docker_errors.DockerException, ConnectionError) as e:
        logger.error(f"Docker is not available: {e}")
        return False

    custom_image_name = f"postgres-hypopg:{version}"

    # Check if image already exists
    try:
        client.images.get(custom_image_name)
        logger.info(f"✓ Image {custom_image_name} already exists")
        return True
    except docker_errors.ImageNotFound:
        pass

    # Build the image
    logger.info(f"Building Docker image: {custom_image_name}")
    try:
        from pathlib import Path

        current_dir = Path(__file__).parent.absolute()
        dockerfile_path = current_dir / "Dockerfile.postgres-hypopg"

        if not dockerfile_path.exists():
            logger.error(f"Dockerfile not found at {dockerfile_path}")
            return False

        logger.info(f"Using Dockerfile: {dockerfile_path}")

        # Build the image
        image, build_logs = client.images.build(
            path=str(current_dir),
            dockerfile="Dockerfile.postgres-hypopg",
            buildargs={"PG_VERSION": version, "PG_MAJOR": version},
            tag=custom_image_name,
            rm=True,
        )

        # Print build logs
        for log in build_logs:
            if "stream" in log:
                logger.debug(log["stream"].strip())

        logger.info(f"✓ Successfully built image {custom_image_name}")
        return True

    except Exception as e:
        logger.error(f"✗ Failed to build Docker image {custom_image_name}: {e}")
        return False


def main() -> int:
    """Main function to build all required Docker images."""
    logger.info("Preparing Docker images for testing...")
    logger.info("")

    versions = ["15", "16"]
    success_count = 0

    for version in versions:
        logger.info(f"Processing PostgreSQL {version}...")
        if build_postgres_image(version):
            success_count += 1
        logger.info("")

    if success_count == len(versions):
        logger.info(f"✓ All {success_count} images built successfully!")
        return 0
    logger.error(f"✗ Only {success_count}/{len(versions)} images built successfully")
    return 1


if __name__ == "__main__":
    sys.exit(main())
