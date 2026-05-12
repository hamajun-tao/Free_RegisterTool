import os
import sys

# 将项目根目录加入 sys.path，解决直接运行 pytest 时可能出现的 ModuleNotFoundError (core/platforms/services/api 等)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
