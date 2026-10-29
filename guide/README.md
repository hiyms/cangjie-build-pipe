# cangjie-guide

在本机通过 `gh` CLI 驱动本仓库的两个 Azure 工作流，不用打开浏览器，也不用记工作流输入的名字。

这是一个独立项目：自己的 `pyproject.toml`、`uv.lock`、虚拟环境与检查配置。它不参与 SDK 构建，
也不被主项目的工具链扫描。

## 安装

```bash
cd guide
uv sync
uv run cangjie-guide --help
```

## 就绪检查

先把本机与目标仓库的配置查一遍，这一步只读，不派发任何东西：

```bash
uv run cangjie-guide verbose
```

它检查 `gh` 是否安装、是否登录、token 是否带 `repo` 与 `workflow`、对仓库是否有写权限、
三个工作流文件是否存在于指定 ref、以及各工作流所需的 secret 是否齐全。

secret 列表需要仓库 admin 权限；拿不到时该项标为「无法验证」并说明原因，不算失败。
有 `fail` 时退出码非零。

## 构建

```bash
uv run cangjie-guide build                        # linux-x64，tag=main，Spot
uv run cangjie-guide build --target windows-x64
uv run cangjie-guide build --compiler 'https://atomgit.com/Cangjie/cangjie_compiler.git#011f9c8'
uv run cangjie-guide build --dry-run              # 只打印将要发送的参数
uv run cangjie-guide build --wait                 # 派发后跟到结束
```

`--compiler` 用的是 `URL#REF`，内部转成工作流要的 `URL:REF`。`#` 避免 URL 自带的冒号产生歧义。

派发前会做两项检查：

**同名产物**。`upload-artifact` 遇到同名产物会覆盖，而版本号是归一化的——`tag=main` 和
`tag=dev` 都变成 `0.0.0-dev`，所以一次重构建会顶掉上一次的归档，即使测试正引用着它。
发现同名未过期产物时列出它的来源与过期时间：非交互环境下报错并提示加 `--force`，
有 TUI 时弹出确认。

**在途运行**。仓库没有声明 `concurrency:`，重复派发不会排队，会再开一台 VM。
两台 16 vCPU 争同一份 `lowPriorityCores` 配额，第二台还可能被静默升到 Regular，价格约五倍。

## 测试

```bash
uv run cangjie-guide test --sdk 40725832486 --suites llt \
  --case testsuites/LLT/compiler/CodegenCJNative/tuple/tuple_ctor.cj
uv run cangjie-guide test --sdk latest --suites llt --changed-in HEAD
uv run cangjie-guide test --sdk https://example.com/cangjie-sdk-linux-x64.tar.gz --suites hlt
```

`--sdk` 接受四种来源：

| 写法 | 含义 |
| --- | --- |
| `40725832486` | 该运行里的 linux 产物 |
| `latest` | 最近一次成功的构建 |
| `cangjie-linux-x64-0.0.0-dev` | 产物名 |
| `https://...` | 直链，走 `sdk_url` |

派发前会拒绝这些组合：`sdk_artifact` 与 `sdk_url` 同时给出、`changed_in` 与 `--case` 同时给出、
`suites` 含 `conformance` 却用 `vm_size=auto`、以及一个用例都没选中的空选择
（空选择在两个测试框架里都会以退出码 0 结束，看起来像通过）。

## 查看

```bash
uv run cangjie-guide runs                 # 最近运行
uv run cangjie-guide status 40725832486   # 作业与当前步骤
uv run cangjie-guide status 40725832486 --watch
uv run cangjie-guide logs 40725832486
uv run cangjie-guide logs 40725832486 --job <job-database-id>
uv run cangjie-guide artifacts 40725832486
uv run cangjie-guide artifacts --search   # 所有未过期的 linux SDK 产物
uv run cangjie-guide artifacts 40725832486 --get cangjie-linux-x64-0.0.0-dev
```

产物下载到 `~/.cache/cangjie-guide/<run-id>/`，不落进工作树。

`cancel` 只提交取消请求。虚拟机由 cleanup 作业删除，那个作业可能失败；泄漏的资源组由每小时
运行的清理工作流兜底，最坏要等八小时。

## 交互式引导

参数不足时会弹出选择界面，参数齐全则直接执行。`--tui` 强制引导，`--no-tui` 禁止引导
（参数不足就报错退出）。输出不是终端时不会尝试引导。

## 设计上刻意不做的事

- **不列出可派发的工作流全集**。`reap-orphans.yml` 是 `workflow_dispatch: {}`，零输入，
  会对每个创建超过八小时的 `cangjie-*` 资源组执行 `az group delete`。零输入意味着界面上
  没有任何东西拦在一次点击前面，所以它不在可派发清单里。
- **不调用 `az`**。所有云操作都发生在工作流里，本机只跟 GitHub 对话。
- **不从工作区读工作流定义**。表单按 dispatch ref 拉取 `gh api .../contents/...?ref=`，
  本地分支或未提交改动不会让表单描述一个与实际运行不同的工作流。
- **不吞掉 `gh` 的失败**。错误分三类：未就绪（日志还没生成）、可重试（5xx、限流）、致命。
  只有前两类会被等待或重试。

## 开发

```bash
cd guide
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run ty check
uv run pytest
```

工作流输入的默认值与选项从 `.github/workflows/*.yml` 读取，不硬编码。工作流改了默认值，
这里跟着走。
