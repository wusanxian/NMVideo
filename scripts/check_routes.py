"""前后端路由一致性检查（零依赖）

前端 api.js 用 `request(path)`，实际请求是 `/api` + path；
后端各 router 自带 prefix。这里把两边归一化成「方法 + /api/... 模板」比对，
找出前端会 404 的调用（页面点了没反应/报错的主要来源之一）。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_JS = ROOT / "frontend" / "src" / "api.js"
ROUTERS = ROOT / "backend" / "app" / "routers"


def _strip_template(expr: str) -> str:
    """把 ${...}（可能嵌套三元/花括号）整体替换成 {}。"""
    out, i = [], 0
    while i < len(expr):
        if expr.startswith("${", i):
            depth, j = 1, i + 2
            while j < len(expr) and depth:
                if expr[j] == "{":
                    depth += 1
                elif expr[j] == "}":
                    depth -= 1
                j += 1
            out.append("{}")
            i = j
        else:
            out.append(expr[i])
            i += 1
    return "".join(out)


def norm(path: str) -> str:
    path = _strip_template(path)                                # JS 模板变量
    path = path.split("?")[0]                                   # 查询串不参与路由匹配
    path = re.sub(r"\{[^}]*\}", "{}", path)                     # FastAPI 路径参数
    path = re.sub(r"/+", "/", path)
    return path.rstrip("/") or "/"


# ---- 后端路由 ----
backend = {}
for f in list(ROUTERS.glob("*.py")) + [ROOT / "backend" / "app" / "main.py"]:
    text = f.read_text(encoding="utf-8")
    prefixes = {}
    for m in re.finditer(r"(\w+)\s*=\s*APIRouter\((.*?)\)", text, re.S):
        var, args = m.group(1), m.group(2)
        pm = re.search(r'prefix\s*=\s*["\']([^"\']*)["\']', args)
        prefixes[var] = pm.group(1) if pm else ""
    for m in re.finditer(r"@(\w+)\.(get|post|put|delete|patch|head|options)\(\s*[\"']([^\"']*)[\"']", text):
        var, method, path = m.group(1), m.group(2).upper(), m.group(3)
        full = norm(prefixes.get(var, "") + path)
        backend.setdefault((method, full), f"{f.name}")

# ---- 前端调用 ----
src = API_JS.read_text(encoding="utf-8")
body_start = src.index("export const api")
snippet = src[body_start:]

entries = []
for m in re.finditer(r"^  (\w+):\s*(?:async\s*)?\(([^)]*)\)\s*=>\s*", snippet, re.M):
    start = m.end()
    nxt = re.search(r"^  \w+:\s*", snippet[start:], re.M)
    body = snippet[start: start + (nxt.start() if nxt else len(snippet))]

    path_m = re.search(r"(?:request|fetch)\(\s*([`\"'])(.*?)\1", body, re.S)
    if not path_m:
        continue
    raw = path_m.group(2)
    method = "GET"
    mm = re.search(r"method:\s*['\"](\w+)['\"]", body)
    if mm:
        method = mm.group(1).upper()
    # fetch 是绝对路径；request 会拼 BASE=/api
    absolute = path_m.group(0).lstrip().startswith("fetch")
    entries.append((m.group(1), method, raw, absolute))

problems = []
for name, method, raw, absolute in entries:
    if absolute:
        full = norm(raw)
    else:
        if raw.startswith("/api/"):
            problems.append(f"{name}: 路径已含 /api，会被拼成 /api/api/... → {raw}")
            continue
        full = norm("/api" + raw)
    if (method, full) not in backend:
        same = sorted(k[0] for k in backend if k[1] == full)
        hint = f"（该路径后端只有 {same}）" if same else ""
        problems.append(f"{name}: {method} {full} 后端无此路由{hint}")

print(f"前端 api 方法 {len(entries)} 个，后端路由 {len(backend)} 条")
if problems:
    print("\n== 可疑调用 ==")
    for p in problems:
        print("  " + p)
else:
    print("全部对得上")
