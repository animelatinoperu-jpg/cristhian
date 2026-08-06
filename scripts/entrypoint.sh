#!/bin/sh
set -eu

if [ "$(id -u)" = "0" ]; then
    if [ -n "${PERSIST_ROOT:-}" ]; then
        mkdir -p "$PERSIST_ROOT" "${DJANGO_MEDIA_ROOT:-$PERSIST_ROOT/private}"
        seed_marker="$PERSIST_ROOT/.initial-data-loaded"

        if [ ! -f "$seed_marker" ] && [ -f /app/deploy_seed/db.sqlite3 ]; then
            cp -f /app/deploy_seed/db.sqlite3 "${SQLITE_PATH:-$PERSIST_ROOT/db.sqlite3}"

            if [ -d /app/deploy_seed/private ]; then
                cp -R /app/deploy_seed/private/. "${DJANGO_MEDIA_ROOT:-$PERSIST_ROOT/private}/"
            fi

            touch "$seed_marker"
        fi

        chown -R app:app "$PERSIST_ROOT"
    fi

    exec gosu app "$0" "$@"
fi

python manage.py migrate --noinput
python manage.py ensure_reference_data
python manage.py collectstatic --noinput
if [ "${SKIP_SEED_DEMO:-0}" != "1" ]; then
    if [ -n "${ADMIN_PASSWORD:-}" ]; then
        python manage.py seed_demo --username "${ADMIN_USERNAME:-admin}" --password "$ADMIN_PASSWORD"
    else
        python manage.py seed_demo --username "${ADMIN_USERNAME:-admin}"
    fi
fi
exec "$@"
