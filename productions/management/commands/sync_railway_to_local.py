"""sync_railway_to_local -- Descarga producciones desde Railway via API HTTP."""

import json
import os
import re
import urllib.request
import http.cookiejar

from django.core.management.base import BaseCommand, CommandError
from django.core import serializers
from django.db import transaction
from django.apps import apps

RAILWAY_URL = os.environ.get("RAILWAY_API_URL", "https://web-production-498f1.up.railway.app")
SYNC_USER = os.environ.get("SYNC_USER", "admin")
SYNC_PASS = os.environ.get("SYNC_PASS", "Diego2026")


class Command(BaseCommand):
    help = __doc__

    def handle(self, *args, **options):
        ProductionOrder = apps.get_model("productions", "ProductionOrder")

        self.stdout.write(f"Conectando a {RAILWAY_URL}...")
        opener = self._login()

        url = f"{RAILWAY_URL}/api/sync-data/"
        self.stdout.write(f"Descargando {url} ...")
        req = urllib.request.Request(url)
        with opener.open(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode())

        data = payload.get("data", payload if isinstance(payload, str) else "[]")
        if isinstance(data, str):
            data = data

        all_objects = list(serializers.deserialize("json", data)) if data else []
        productions = [obj.object for obj in all_objects if isinstance(obj.object, ProductionOrder)]
        children = [obj.object for obj in all_objects if not isinstance(obj.object, ProductionOrder)]

        local_ids = set(ProductionOrder.objects.using("default").values_list("pk", flat=True))
        self.stdout.write(f"Railway: {len(productions)}  |  Local: {len(local_ids)}")

        new = [p for p in productions if p.pk not in local_ids]
        if not new:
            self.stdout.write(self.style.SUCCESS("Todo sincronizado."))
            return

        copied = 0
        for prod in new:
            label = f"PP-{prod.number}/{str(prod.created_at.year)[-2:]}"
            sid = transaction.savepoint(using="default")
            try:
                prod.pk = None
                prod._state.db = "default"
                prod.save(using="default")

                transaction.savepoint_commit(sid, using="default")
                copied += 1
                self.stdout.write(f"  [OK] {label}")
            except Exception as e:
                transaction.savepoint_rollback(sid, using="default")
                self.stdout.write(self.style.WARNING(f"  [SKIP] {label}: {e}"))

        self.stdout.write(self.style.SUCCESS(f"Sync: {copied} producciones."))

    def _login(self):
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

        login_url = f"{RAILWAY_URL}/cuentas/login/"
        with opener.open(login_url, timeout=30) as resp:
            html = resp.read().decode()
            csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', html)
            if not csrf:
                raise CommandError("No CSRF token.")
            token = csrf.group(1)

        data = urllib.parse.urlencode({
            "username": SYNC_USER, "password": SYNC_PASS,
            "csrfmiddlewaretoken": token,
        }).encode()
        req = urllib.request.Request(login_url, data=data)
        req.add_header("Referer", login_url)
        with opener.open(req, timeout=30) as resp:
            pass  # session cookie set

        self.stdout.write("Autenticado.")
        return opener
