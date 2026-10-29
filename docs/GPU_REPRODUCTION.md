# 四卡GPU模型栈独立复现

正式方案始终使用本地GPU模型。此前的CPU记录只验证基础安装、PDF解析和持久化，不是系统被改成CPU版本。2026-10-29继续在四张RTX2080Ti上建立独立代码目录、独立虚拟环境和独立文献库，完成Qwen、E5与EasyOCR的真实联合验收。

## 环境与隔离

- 代码基线为50231705d9ed1807f186b38818682fb557517705，使用git archive导出，不复制原.venv、数据库或.env。
- Python3.12.14；90个依赖版本与backend/server-environment.lock.txt一致，uv依赖兼容性检查通过。新环境的sys.path不含原虚拟环境，torch模块从新环境加载。
- PyTorch2.13.0+cu130、CUDA13.0、Transformers5.17.0、EasyOCR1.7.2；四卡均被识别为RTX2080Ti，计算能力7.5。
- 使用既有模型权重，28项文件哈希与Qwen/E5及OCR清单完全一致；服务禁止联网下载权重，没有调用云模型。
- 隔离服务只监听127.0.0.1:18476，采用全新数据目录；不读取生产.env，不连接生产API，不改变已有文献的归档或版本选择。

依赖首次离线解析缺少索引元数据，联网解析后又遇到两包下载缓慢。本次从既有注册表安装缓存中选择兼容版本，校验27403项RECORD哈希并保留文件权限，重建90个本地Wheel后使用uv正常安装；没有从原虚拟环境复制包。此结果不等于从零公网下载或全新操作系统安装。

## GPU分工与实测

| 设备  | 任务                  | 模型就绪时显存 | 完成回归时显存 |
| ----- | --------------------- | -------------: | -------------: |
| GPU 0 | Qwen2.5-7B分片        |       6536 MiB |       7914 MiB |
| GPU 1 | Qwen2.5-7B分片        |       8314 MiB |       9690 MiB |
| GPU 2 | multilingual-e5-small |        622 MiB |        950 MiB |
| GPU 3 | EasyOCR检测与识别     |        340 MiB |       2980 MiB |

分工来自与现有部署相同的显式加载配置，并与该隔离服务进程的四卡显存快照对照。表中是本次采样值，不是峰值或最低资源承诺。OCR执行中的一次快照记录到GPU3利用率21%；没有用显卡数量代替实际模型运行。

1. 原生Transformer v7：15页解析、47/47段E5向量、原文件哈希核对通过。真实Qwen回答FFN内部维度为2048，引用物理第5页原生正文。
2. 官方扫描件：确认15页均无文本层，15页全部经GPU OCR，保存文字、坐标和置信度；43/43段E5向量，原件哈希核对通过。进入相似版本确认时，仅在此隔离库显式保留两版。真实Qwen同样回答2048，引用物理第5页OCR正文。
3. BERT：16页解析、72/72段E5向量；与Transformer的双篇架构问题分别引用两篇第3页，准确区分编码器与解码器，归纳结论有明确标记。该题走确定性事实处理器，不能单独计作Qwen生成验证。

三份文献共46个物理页、162/162段向量。所有向量为384维FP32，数据库Blob均为1536字节；这是只读数据库核对，不是仅凭health中的E5就绪状态推断。两条Qwen问答分别约4.28秒、2.77秒，仅为本轮样例耗时，不作为性能承诺。

## 复现命令

新目录安装完整GPU依赖，模型路径使用已有权重或另行完成下载与清单核对：

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python \
  -r backend/server-environment.lock.txt \
  --extra-index-url https://download.pytorch.org/whl/cu130
uv pip check --python .venv/bin/python
```

在独立目录、干净进程环境启动；下面仅示意本轮配置，模型路径应替换为实际外置目录：

```sh
CUDA_VISIBLE_DEVICES=0,1,2,3 \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false \
YANXI_LLM_PATH=/外置模型目录/Qwen2.5-7B-Instruct YANXI_LLM_SHARD=1 \
YANXI_EMBEDDING_PATH=/外置模型目录/multilingual-e5-small YANXI_EMBEDDING_DEVICE=cuda:2 \
YANXI_OCR_PATH=/外置模型目录/easyocr YANXI_OCR_DEVICE=cuda:3 \
YANXI_DATA_DIR=runtime/isolated-library \
  .venv/bin/python -m uvicorn backend.app:app --host 127.0.0.1 --port 18476
```

然后在可访问该回环服务的机器上运行：

```sh
.venv/bin/python scripts/verify_gpu_stack.py \
  --base-url http://127.0.0.1:18476 \
  --fixture-dir /你的官方资料目录/pdf \
  --output runtime/gpu-repro/API_REPORT.json \
  --isolated-library --keep-scan --with-bert
```

服务必须先启动，且文献库最初为空。脚本拒绝18474正式入口及非回环地址；只有显式keep-scan才会对本轮扫描件的候选保留两版，不自动删除任何文献。失败记录保留，不能在非空库直接重跑整套来掩盖失败。脚本本身不负责启动、停止模型或释放GPU，运行者需在结束后停止隔离服务。

完整结果、依赖版本、哈希依据、显存采样、向量行数和问答原文坐标见[GPU验收记录](GPU_STACK_VERIFICATION_20261029.json)。本轮测试结束后已停止临时服务、关闭SSH转发，四卡显存回到空闲；保留独立环境以便再次验证。

## 边界与下一步

这是复用主机OS、驱动、解释器及模型权重的独立包环境与API验收。只测试三份指定PDF和三道问题，不能据此声称全部官方样本通过、OCR准确率或零幻觉。此轮没有重测浏览器、翻译和中文研究任务，也不等于已完成公网演示与提交材料。团队信息按参赛者确认暂标待补。

## 随后补齐正式库的历史向量

独立复现结束后，只读检查正式库发现HellaSwag此前的CPU导入只有BM25文本，63段均缺少向量。先备份数据库，再启动正式四卡服务，通过现有reindex接口重新解析并生成E5向量，63/63段均为384维FP32。

完成后核对原PDF哈希、14页正文哈希、33份文献的状态/归档/有效版本选择均保持一致；所有当前有效ready文献已无缺失向量。此步骤是另一次有备份的正式库维护，不属于上面的隔离库测试；归档文献不自动重建。完成后再次停止模型服务释放显存，见[向量补齐记录](EMBEDDING_REPAIR_20261029.json)。
