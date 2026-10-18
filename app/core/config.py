import os

DAY1_SALT = os.environ.get("DAY1_SALT", "")
DAY2_SALT = os.environ.get("DAY2_SALT", "")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://nascon:nascon@db:5432/nascon")
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
JWT_SECRET = os.environ.get("JWT_SECRET", "nascon26-local-jwt-secret-change-me")
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "minio:9000")
MINIO_ROOT_USER = os.environ.get("MINIO_ROOT_USER", "nascon-minio")
MINIO_ROOT_PASSWORD = os.environ.get("MINIO_ROOT_PASSWORD", "nascon-minio-secret")

MAX_BODY_BYTES = 1024 * 1024
DEFAULT_SEED = 42
