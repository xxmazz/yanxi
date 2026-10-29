# 独立环境基础复现

2026-10-29，从固定提交f2a9eafd78adf66c44c9abd04281989ae276681b用git archive创建新目录，重新建立Python3.12虚拟环境并安装backend/requirements.txt，以npm ci安装前端并构建。不复制既有node_modules、.venv、runtime或.env；依赖工具可使用正常下载缓存，因此不算全新操作系统或完全离线复现。

```sh
git archive f2a9eafd78adf66c44c9abd04281989ae276681b | tar -x -C 你的空目录
cd 你的空目录
uv venv --python 3.12
uv pip install --python .venv/bin/python -r backend/requirements.txt
npm ci
npm run build
YANXI_DATA_DIR=runtime-cpu-smoke YANXI_DIST_DIR=dist \
  CUDA_VISIBLE_DEVICES= HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 18475
```

该示例假定当前shell没有导出任何模型或OCR路径；本次实际验收使用经过清理的子进程环境，没有加载权重。完整模型版按[本地后端](LOCAL_BACKEND.md)另装GPU依赖和预下载权重，本轮不替代GPU及OCR安装复现。

## 实际结果

- Python3.12.13、Node24.21.0、npm11.19.0；新建环境与基础依赖安装约3秒，npm安装及构建约11秒，仅记录本次耗时。
- 使用用户提供的官方Transformer v7原件副本，15页全部解析；原文件下载SHA-256与入库身份一致。
- 核对40条书目、57处正文引用、24个目录项，共121个原文区间和页面哈希；改名重复上传仍复用同一文献。
- 使用可用的Poppler渲染原页PNG；重启后完整文献记录、导航结果与原件哈希保持一致。
- 未加载模型时，需推理的问答明确返回503，没有将CPU解析成功当作模型问答通过。
- 固定提交的133项后端测试通过。所有临时监听进程均停止，未连接生产服务器或使用GPU。

完整的脱敏命令、版本、结果与限制见[复现记录](CLEAN_REPRODUCTION_20261029.json)。认证功能是随后新增的代码，使用单独的[访问控制验证](REVIEW_ACCESS.md)，不把固定提交的结果当作新增代码已在新环境复现。

还需验证完整GPU依赖与模型权重在独立环境的安装运行、OCR、真实模型问答和浏览器交互。公网部署与正式提交材料亦不在此轮基础复现范围内。
