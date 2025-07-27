from decouple import config

from django.conf import settings

INTERNAL_IPS = [
    "127.0.0.1",
    "localhost",
    
]


ALLOWED_HOSTS = ["*"]

STATIC_URL = "/static/"
MEDIA_URL = "/media/"


CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379",
    },
}

