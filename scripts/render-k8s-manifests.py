#!/usr/bin/env python3
"""Render every role's k8s manifest templates with dummy secrets, so CI can
schema-validate the final YAML (kubeconform) instead of linting .j2 soup.

Secrets and host-derived values get stand-ins: schema validation cares about
structure, not real tokens. Any unresolved default stays a literal string,
which is fine for string-typed fields.

Usage: render-k8s-manifests.py <output-dir>
"""
import hashlib
import json
import os
import sys

import yaml
from jinja2 import Environment, FileSystemLoader

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROLES_DIR = os.path.join(REPO, "ansible", "roles")
K8S_ROLES = ["monitoring", "logging", "nginx", "sandstorm"]

# Stand-ins for vaulted / host-derived / inventory values.
DUMMY = {
    "grafana_admin_password": "dummy",
    "telegram_bot_token": "dummy",
    "telegram_chat_id": "-100",
    "grafana_root_url": "https://example.org:3443/",
    "nginx_enable_tls": True,
    "nginx_server_name": "example.org",
    "nginx_https_port": 3443,
    "sandstorm_runtime": "k3s",
    "inventory_hostname": "vps",
    "getent_passwd": {"steam": ["x", "1001", "1001", "", "/home/steam", "/bin/bash"]},
}


def load_context():
    """Merge every role's defaults, then resolve jinja references between
    them ({{ sandstorm_home }}/... etc.) by re-rendering until stable."""
    ctx = {}
    for role in os.listdir(ROLES_DIR):
        defaults = os.path.join(ROLES_DIR, role, "defaults", "main.yml")
        if os.path.exists(defaults):
            with open(defaults) as f:
                ctx.update(yaml.safe_load(f) or {})
    ctx.update(DUMMY)

    env = Environment()
    for _ in range(3):  # defaults nest at most a couple of levels deep
        changed = False
        for key, value in ctx.items():
            if isinstance(value, str) and "{{" in value:
                rendered = env.from_string(value).render(**ctx)
                if rendered != value:
                    ctx[key] = rendered
                    changed = True
        if not changed:
            break
    return ctx


def render_role(role, ctx, out_dir):
    tpl_dir = os.path.join(ROLES_DIR, role, "templates")
    k8s_dir = os.path.join(tpl_dir, "k8s")
    env = Environment(loader=FileSystemLoader([tpl_dir, k8s_dir]))
    env.filters["hash"] = lambda v, alg: hashlib.new(alg, v.encode()).hexdigest()
    env.filters["dirname"] = os.path.dirname
    env.filters["to_json"] = json.dumps

    def lookup(kind, name):
        for base in (tpl_dir, os.path.join(ROLES_DIR, role, "files")):
            path = os.path.join(base, name)
            if os.path.exists(path):
                with open(path) as f:
                    src = f.read()
                if kind == "template":
                    return env.from_string(src).render(**ctx, lookup=lookup)
                return src
        raise FileNotFoundError(f"{role}: lookup('{kind}', '{name}')")

    rendered = []
    for tpl in sorted(os.listdir(k8s_dir)):
        if not tpl.endswith(".j2"):
            continue
        out = env.get_template(tpl).render(**ctx, lookup=lookup)
        docs = [d for d in yaml.safe_load_all(out) if d]  # parse = first gate
        dest = os.path.join(out_dir, f"{role}-{tpl[:-3]}")
        with open(dest, "w") as f:
            f.write(out)
        rendered.append((tpl, [d["kind"] for d in docs]))
    return rendered


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "_rendered"
    os.makedirs(out_dir, exist_ok=True)
    ctx = load_context()
    total = 0
    for role in K8S_ROLES:
        for tpl, kinds in render_role(role, ctx, out_dir):
            print(f"{role}/{tpl}: {kinds}")
            total += len(kinds)
    print(f"OK: {total} k8s objects rendered to {out_dir}/")


if __name__ == "__main__":
    main()
