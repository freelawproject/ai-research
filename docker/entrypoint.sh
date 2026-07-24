#!/bin/sh
set -e

case "$1" in
    web-dev)
        exec python manage.py runserver 0.0.0.0:8000
        ;;
    *)
        exec "$@"
        ;;
esac
