"""本地测试总入口：一条命令跑完 backend/tests 下的全部用例。

为什么不是 pytest：本机 pip 走 HTTPS 常报 SSL 错，pytest 装不上；这里用
标准库 unittest 驱动 TestCase，另外补一个最小 `monkeypatch` 兼容层，让
按 pytest 风格写的模块级 `test_*` 函数也能跑（本项目 tests 目录不入库，
仅本地保留）。

用法：python tests/run_all.py [-v]
"""
import importlib
import importlib.util
import sys
import traceback
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import _stubs  # noqa: E402

_stubs.install()


class MonkeyPatch:
    """够用就行的 monkeypatch：setattr / setitem / setenv / delenv。"""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value, raising=True):
        had = hasattr(target, name)
        if not had and raising:
            raise AttributeError(f"{target!r} 没有属性 {name}")
        old = getattr(target, name, None)
        self._undo.append(("attr", target, name, old, had))
        setattr(target, name, value)

    def setitem(self, mapping, key, value):
        had = key in mapping
        old = mapping[key] if had else None
        self._undo.append(("item", mapping, key, old, had))
        mapping[key] = value

    def setenv(self, name, value):
        import os

        had = name in os.environ
        self._undo.append(("env", os.environ, name, os.environ.get(name), had))
        os.environ[name] = str(value)

    def delenv(self, name, raising=True):
        import os

        had = name in os.environ
        if not had and raising:
            raise KeyError(name)
        self._undo.append(("env", os.environ, name, os.environ.get(name), had))
        os.environ.pop(name, None)

    def undo(self):
        for kind, obj, name, old, had in reversed(self._undo):
            if kind == "item":
                if had:
                    obj[name] = old
                else:
                    obj.pop(name, None)
            elif had:
                setattr(obj, name, old)
            else:
                try:
                    delattr(obj, name)
                except AttributeError:
                    pass
        self._undo.clear()


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"t_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_module_functions(module) -> tuple:
    """跑模块级 test_* 函数（pytest 风格），返回 (passed, failed)。"""
    passed = failed = 0
    for name in sorted(dir(module)):
        if not name.startswith("test_") or not callable(getattr(module, name)):
            continue
        fn = getattr(module, name)
        if not getattr(fn, "__module__", "") == module.__name__:
            continue
        code = fn.__code__
        mp = MonkeyPatch()
        kwargs = {"monkeypatch": mp} if "monkeypatch" in code.co_varnames[:code.co_argcount] else {}
        try:
            fn(**kwargs)
            passed += 1
            print(f"  ok   {name}")
        except Exception:
            failed += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
        finally:
            mp.undo()
    return passed, failed


def main() -> int:
    verbose = "-v" in sys.argv
    files = sorted(p for p in HERE.glob("test_*.py"))
    total_pass = total_fail = 0
    for path in files:
        print(f"\n=== {path.name}")
        module = _load(path)
        has_cases = any(
            isinstance(obj, type) and issubclass(obj, unittest.TestCase)
            for obj in vars(module).values()
        )
        if has_cases:
            suite = unittest.defaultTestLoader.loadTestsFromModule(module)
            result = unittest.TextTestRunner(verbosity=2 if verbose else 1).run(suite)
            total_pass += result.testsRun - len(result.failures) - len(result.errors)
            total_fail += len(result.failures) + len(result.errors)
        p, f = run_module_functions(module)
        total_pass += p
        total_fail += f
    print(f"\n汇总：通过 {total_pass}，失败 {total_fail}")
    return 0 if total_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
