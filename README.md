# cangjie-build

[仓颉（Cangjie）](https://github.com/CangjieLanguage) SDK 的工程化构建流水线，使用
Python + [uv](https://docs.astral.sh/uv/) 实现，并配套 GitHub Actions 工作流。

## 支持的目标

| Target        | 模式                                              |
| ------------- | ------------------------------------------------- |
| `linux-x64`   | 原生构建（Ubuntu 22.04 + clang-15）               |
| `windows-x64` | 通过 llvm-mingw + openssl（mstorsjo）交叉编译     |

> macOS / aarch64 / OHOS 等目标暂未实现，但 `targets.py` 中的策略类已经
> 预留了扩展点。

## 本地快速上手（Ubuntu 22.04）

```bash
uv sync
uv run cangjie-build --target linux-x64 install-system-deps
uv run cangjie-build --target linux-x64 install-static-libs
uv run cangjie-build --target linux-x64 fetch
uv run cangjie-build --target linux-x64 build compiler
uv run cangjie-build --target linux-x64 build runtime
uv run cangjie-build --target linux-x64 build stdlib
uv run cangjie-build --target linux-x64 build stdx
uv run cangjie-build --target linux-x64 build tools
uv run cangjie-build --target linux-x64 package
uv run cangjie-build --target linux-x64 verify
```

`run-all` 子命令把以上步骤串成一条线（主要用于本地调试）。`windows-x64`
目标把 `install-static-libs` 换成 `install-mingw`，并额外跑
`install-target-python`（从 NuGet 拉一份 Windows Python 摆好布局供
`BuildCJDB.cmake` 通过 `TARGET_PYTHON_PATH` 链接 lldb，并把
`python<M><N>.dll` / `vcruntime140*.dll` 落到 `output/tools/bin/`）。

## 交叉编译涉及的上游补丁

`fetch` 阶段克隆 cangjie 源码后，对 `cangjie_compiler` 应用两处 cmake
补丁，把 lldb 的 `ExternalProject_Add` 真正卡在 `cangjie-frontend` /
`cangjie-lsp-share` 之后（上游只挂在 ExternalProject 顶层 target 上，
CMP0114 OLD 行为下卡不住 build step，会出现 `libcangjie-frontend.dll.a
missing` 的竞态）：

- `third_party/cmake/BuildCJDB.cmake`：加 `cmake_policy(SET CMP0114 NEW)`
  + `STEP_TARGETS build configure`
- `src/CMakeLists.txt`：在 `CANGJIE_BUILD_CJDB` 块里追加
  `add_dependencies(lldb-build cangjie-frontend cangjie-lsp-share)`

`tools` 阶段对 `cjpm/build/build.py` 应用一处补丁：把 cross-windows
链接行硬编码的 `/opt/buildtools/llvm-mingw-w64/x86_64-w64-mingw32/lib`
改为 `$MINGW_PATH/x86_64-w64-mingw32/lib`，这样不依赖上游
`linux_cross_windows_zh.md` 假设的 `/opt/buildtools` 路径。

补丁逻辑统一走 `_common.apply_text_patch()`，匹配不到旧字串会抛
`BuildError("...patch shape drift...")`，让上游重命名时大声失败而不是
静默 no-op。

windows 交叉编译的 build_type 在 `cfg.cross_build_type` 里硬编码为
`release`：上游 `relwithdebinfo` 路径在 MinGW 上有多处问题
（`src/CMakeLists.txt:272` 把 cangjie-frontend 切到静态 `.a`，
`-fdebug-types-section` 被注入 pcre2 但 clang 在 `x86_64-w64-windows-gnu`
上拒绝），与 linux-host 的 `--build-type` 选择无关。

链接器统一走 lld：`base_env` 设 `LDFLAGS=-fuse-ld=lld`，cmake 在首次
configure 时把它拷进 `CMAKE_*_LINKER_FLAGS_INIT`；llvm-mingw 自带
lld，cangjie 自带的 clang 在 cjc emit 时也用 lld。

## 持续集成

仓库提供两条构建工作流：

- `.github/workflows/build-cangjie.yml` — GitHub Actions 托管 runner 上
  的矩阵构建（`linux-x64` + `windows-x64`），多级 sccache（disk L0 +
  GHA L1）。`workflow_dispatch` 触发器参数与上游 `tozi-team/cangjie_build`
  对齐：全局 `tag`、按仓库覆盖的 URL/tag、`build_type`、`run_tests`，另加
  `version`（见下）。
- `.github/workflows/build-cangjie-azure.yml` — Azure ephemeral VM 跑
  `windows-x64` cross-compile（默认 `Standard_F16als_v7` 的 Spot 实例，区域
  fallback `eastus → eastus2 → centralus → westus3`）。GHA cache API 在重活下
  会 429 限流，改成把 sccache 写入 Azure Blob 容器
  （`SCCACHE_AZURE_BLOB_CONTAINER=sccache`）。

两条工作流都有 `version` 输入：它决定写进 `-v` 与产物名字的版本号，`tag` 只决定源码 ref。
留空则按 `tag` 推导（分支名归一化为 `0.0.0-dev`）。填了就必须是不带前导 `v` 的
MAJOR.MINOR.PATCH，否则 `Resolve effective Cangjie version` 这一步会在 provision 之前失败——
harness 会默默剥掉 `v`、把其余不合法输入换成 `0.0.0-dev`，产物就会以另一个版本的名字发布。

`.github/workflows/reap-orphans.yml` 是 Azure 的安全网：定时清理被取消或异常
中断的 `cangjie-*` Azure resource group。项目只支持 Azure，GCP 相关的工作流和
脚本已移除。

### 缓存与凭据

sccache 和交叉编译工具链都放在 Azure Blob 容器里，不用 GitHub Actions cache：
后者对单个仓库有 10 GB 上限，重活下还会对写入限流（sccache 当初就是因为这个
换掉的）。工具链是一棵约 2 GB 的树，只随 LLVM/openssl 的固定版本变化，适合当成
普通 blob 对象。

工具链的 blob 名由它的固定参数推导（`cangjie-build toolchain-cache-key`）：
llvm-mingw 的 tag、LLVM 的 commit、mingw-w64 的 commit、openssl 的版本、target
triple，加上各自的配方版本号（`RECIPE_VERSION`，改了 configure 参数或环境变量就
手动 +1）。改动其中任何一项，名字就变了，旧对象立即不再被命中——不需要手动记得去改
key。推导放在 Python 里而不是脚本里，就是为了让版本号和 blob 名不可能对不上。

mingw-w64 原本是按远端默认分支 `--depth 1` 克隆的，没有固定 commit。那意味着上游
一有新提交，产出的工具链就变了而 key 不变，缓存会返回一份来路不明的产物。现在它
和 LLVM 一样固定到具体 commit，也纳入 key。

存储账户有一条生命周期规则，`toolchain` 容器里超过 60 天的对象会被删除。这样被
新版取代的旧工具链不会一直占着空间。

| secret | 用途 |
| --- | --- |
| `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` | OIDC 登录，无静态密码 |
| `CANGJIE_CACHE_CONNECTION_STRING` | 工具链 blob |
| `SCCACHE_AZURE_CONNECTION_STRING` | sccache blob |
| `RUNNER_REGISTRATION_PAT` | 生成 JIT runner 配置，需要仓库的 Administration: Write |

OIDC 需要 app registration 上的 federated credential 指向本仓库，subject 用
`repo:<owner>/<repo>:ref:refs/heads/master`（本仓库默认分支是 `master`）。
没有这条信任时 `azure/login` 会失败，而报错信息并不指向原因。

`RUNNER_REGISTRATION_PAT` 不能用 `GITHUB_TOKEN` 替代：JIT runner 注册要
`Administration: Write`，而 `GITHUB_TOKEN` 拿不到这个 scope。

> 这些工作流尚未实际派发过。本地验证覆盖的是命令语义：参数拼接、路径解析、归档
> 解包、真实浅克隆、结果判定、分档计数。Azure 上的 provision、artifact 下载、
> VM 上的 runner 注册要等第一次启用才能确认。

### Azure 实例与配额

两种优先级都受配额约束，但约束的桶不同：

| 优先级 | 受哪条配额约束 |
| --- | --- |
| Spot | 区域级 `lowPriorityCores` |
| 按需 | 区域级 `cores` **和** 系列级 `<Family>Family` |

Spot 不检查后两条，所以同一个 16 vCPU 的 SKU，可能在按需配额不足的区域反而能用
Spot 跑起来。`azure-provision-vm.sh` 对两种优先级都在提交分配请求前做预检：把容
不下该 SKU 的区域从对应的尝试列表里剔除，避免在一组注定返回 `QuotaExceeded`
的组合上空转 3 个可用区 × N 个区域（实测一次注定失败的 `az vm create` 约 8 秒）。

系列配额名从 SKU 名推出来（`Standard_F16als_v7` → `standardFalsv7Family`），
不调 `az vm list-skus`：那个命令要走一遍完整 SKU 目录，实测单区域会超过 2 分钟。

仓库当前订阅的配额：

| 区域 | `lowPriorityCores` | `cores` | `standardFalsv7Family` |
| --- | --- | --- | --- |
| eastus | 20 | 10 | 10 |
| eastus2 | 3 | 30 | 30 |
| centralus / westus3 / westus2 | 3 | 10 | 10 |

对一个 16 vCPU 的 SKU，这意味着 Spot 只有 `eastus` 能跑，按需只有 `eastus2` 能跑。
32 vCPU 的 SKU 两边都放不下（按需最多 30）。

Spot 的价格约为按需的 18%（`Standard_F16als_v7` 在 eastus2 是 0.179 对 0.968
美元/小时）。代价是随时可能被回收，另外两点实现细节：

- Spot 用 `--eviction-policy Delete`，同时设了 `--os-disk-delete-option
  Delete`：驱逐时操作系统盘随 VM 一起删除，否则每次驱逐会漏下一块
  256 GB 的托管盘。
- 被驱逐时 runner 进程随 VM 一起消失，构建日志会在某一步中间断掉，Actions
  界面上与真正的构建失败无法区分。`azure-eviction-check.sh` 在 cleanup 阶段
  查 Activity Log 里的 `virtualMachines/deallocate` 记录，命中时打一条
  `::notice::`，把“Azure 回收了机器”和“代码构建失败”分开。

### 测试阶段

cangjie_test 的用例跑在 Linux 二进制上，所以 `windows-x64` 的交叉编译产物
无法用来测试。测试因此是单独的工作流 `.github/workflows/test-cangjie-azure.yml`：
它启一台规格小得多的虚拟机，拿一份已编译好的 linux-x64 SDK，然后跑
cangjie_test 的套件。

本地也能直接跑，只要 `workspace/software/cangjie` 里已经有 SDK：

```bash
uv run cangjie-build test --suite conformance --tests 'src/tests/01_lexical_structure/*'
uv run cangjie-build test --suite hlt --test-list testsuites/HLT/testlist -j 16
uv run cangjie-build --workspace ws test --sdk-archive cangjie-sdk-linux-x64-1.2.3.tar.gz
```

只跑单个用例，或者只跑某个 commit 改到的用例：

```bash
# 指定文件或目录，不需要 test list
uv run cangjie-build test --suite llt \
  --case testsuites/LLT/compiler/Mangle/MangledName/mangle04_string_literal.cj

# 跑最后一个 commit 动过的用例（也可给任意 rev）
uv run cangjie-build test --suite hlt --changed-in HEAD
uv run cangjie-build test --suite hlt --changed-in 26760809cf
```

`--case` 和 `--test-list` 里的相对路径先按当前目录找，找不到再按 `cangjie_test`
检出的根目录找。Maple 自己是以 `cangjie_test_framework` 为工作目录启动的，所以
README 里这种写法才能用。

`--changed-in` 用 `git diff-tree --diff-filter=d` 列出该 commit 改动过的文件，只保留
所请求套件下的 `.cj` 和 `.info`，然后取它们的**所在目录**交给 Maple，由框架自己判定
目录里哪个文件是可运行用例。几个边界：

- 已删除的用例不算，删掉的文件跑不了。
- 该 commit 没动可运行的用例时直接报错，不跑空集合。
- merge commit 用 `-m` 按每个父提交分别求差异。不加 `-m` 的话 `git diff-tree -r`
  对 merge 什么都不输出，而上游 `dev` 分支的发布几乎都是 merge，不给 `-m` 等于让
  这个功能在真实分支上不可用。
- 测试仓库是 `--depth 1` 浅克隆的。浅克隆里 `HEAD` 是嫁接边界，`diff-tree` 对它
  不输出任何路径，更早的 rev 本地也不存在。所以解析 rev 之前会按需 `git fetch
  --deepen`；`HEAD` 一定可用，越老的 rev 越可能需要联网补历史。
- 不支持 `conformance`（那个套件是掩码语义，用 `--tests`），也不能与 `--case`
  或 `--test-list` 同时用。

三个套件用的是上游两套不同的框架，行为差别要清楚：

- `conformance` 走 cangjie_test 自带的 `Conformance/Compiler/harness/harness.py`。
  它**总是退出 0**，失败只体现在工作目录的 `results.log.json` 里。CLI 因此回读
  那个 JSON，对 `FAILED` / `ERRORED` / `INCOMPLETE` 主动报错。`INCOMPLETE`
  指用例编译通过但没被执行完，上游自己的报告把它归到 SKIPPED 一类，这里按失败算。
- `hlt` 和 `llt` 走 `cangjie_test_framework`（Maple）。它支持 `--fail_exit`，
  失败用例本身就会返回非零。两个套件的配置并不通用：HLT 用
  `configs/cjnative/linux_x64-linux_x64/basic.cfg` 加 `testlist`，LLT 用
  `configs/cjnative/cjnative_test.cfg` 加 `cjnative_testlist`。
- 两者都有一个共同陷坑：筛选条件匹配不到任何用例时，框架都返回 0。一次
  “零个用例”的运行什么都没验证，所以 CLI 把空结果判为失败。Maple 还有一层
  更隐蔽的：它的 `total` 把 `UNRESOLVED` 和 `NOT_RUN` 也算进去，而
  `--fail_exit` 对这两个不算失败。所以只挑到辅助文件（没有 `EXEC` 头）时，
  Maple 会以 `total: N, UNRESOLVED: N` 返回 0。CLI 因此要求至少有
  `PASS + FAIL > 0` 才算真的跑过。

一次调用可以混合套件，因为 `--case` 的路径自己说明归属：

```bash
uv run cangjie-build test \
  --case testsuites/LLT/compiler/CodegenCJNative/tuple/tuple_ctor.cj \
  --case testsuites/HLT/compiler/cjnative/Chir/ForIn/for_in_01.cj
```

两个套件分别独立运行（各自的 `test_cfg`、工作目录、结果文件都不同），但只需一次
调用。`--suite` 只在整套件运行时需要；给了 `--case` 就以路径为准。

### 按用例数选测试机规格

全量跑和跑两个用例对机器的要求差很多，所以测试机的规格按用例数决定：

| 用例数 | 规格 | Spot 单价（eastus） |
| --- | --- | --- |
| <= 20 | `Standard_F2als_v7`（2 vCPU） | 0.023 美元/小时 |
| <= 1000 | `Standard_F4als_v7`（4 vCPU） | 0.047 美元/小时 |
| 更多 | `Standard_F8als_v7`（8 vCPU） | 0.094 美元/小时 |

`test-cangjie-azure.yml` 的 `vm_size` 默认是 `auto`：provision 之前先跑
`plan-tests` 数一遍用例，再据此创建机器。也可以直接指定规格覆盖。

并行度跟着规格走：2 核用 `-j2`、4 核 `-j4`、8 核 `-j8`。用例之间是独立进程，
worker 数超过核数会过度订阅，把每个用例拖过 `--base-timeout`，把一个慢的运行变成
失败的运行。`jobs` 留空即按规格推导，显式给值则覆盖。

```bash
# 只数用例，不跑、不需要 SDK
uv run cangjie-build plan-tests --case testsuites/LLT/compiler/Mangle/MangledName
# cases=47
# vm_size=Standard_F4als_v7
```

计数只用来选机器规格，所以刻意高估：对每个选中的路径，取 `.info` 数量和 `.cj`
数量里的**较大者**。工具类用例的 `.info` 和它驱动的 `.cj` 是重叠的，相加会重复；
而判断哪个文件才是可运行用例等于重写框架的指令解析器，猜错的方向不对称——少算会
把几百个用例塞到 2 核机器上（慢，还可能触发 `--base-timeout`），多算只多花一档
Spot 的钱。

`conformance` 套件不支持自动计数（它的选择机制是掩码），所以跑 conformance 时必须
显式指定 `vm_size`。注意 `suites` 的默认值恰好是 `conformance`，而 `vm_size` 默认是
`auto`，这两个默认值凑在一起会在选规格那一步直接报错——只跑 conformance 时要同时给
`vm_size`。

不用 B 系列，两个理由。

价格上它没有优势。按 `eastus`（测试机的默认区域）比较，`Standard_B4ms` 按需
0.272 美元/小时，反而比 `Standard_F4als_v7` 的 0.242 更高；`eastus2` 的 B4ms
便宜（0.166），但默认区域不是它。而 Fals_v7 的 Spot 在 eastus 是按需的 19.4%；
B4ms 在价格 API 里根本没有 Spot 条目。

性能上它不可预期。B4ms 是突发型，基线只有 90%，编译这类持续满载的负载会一直
消耗额度（满负载时 `((90x4) - (100x4))/100 = -0.4` 额度/分钟），额度耗尽后会
被限速到基线。限速会让单个用例变慢，可能触碰 `--base-timeout`，测试结果也不再
可复现。

`--suite` 可重复；不指定时只跑 `conformance`。测试仓库首次运行会浅克隆到
`workspace/cangjie_test_checkouts/`（约 1 GB），后续复用。已存在的目录只有在确实
是 git 检出时才复用，避免中断的克隆被当成有效仓库。结果落在
`workspace/test-results/<suite>/`，CI 里会作为 artifact 上传。

`--sdk-archive` 解包前会先清掉已有的 `software/cangjie`（否则两个版本的 SDK 会
混在一起），并检查压缩包成员没有逃出目标目录，解包后还要求
`bin/cjc` 和目标平台的 `runtime/lib/<platform>_cjnative` 都在。传一个
windows-x64 的包给 linux-x64 的运行会在这一步就失败，不会走到每个用例都加载
不到运行库。

conformance 用例对 unused 警告敏感，CLI 固定传 `--cjc-flags=-Woff unused`，
与上游 README 的推荐调用一致。

只要 `which sccache` 成功，CLI 就会通过 cmake 的
`CMAKE_C_COMPILER_LAUNCHER=sccache` /
`CMAKE_CXX_COMPILER_LAUNCHER=sccache` 启用它。注意 sccache 只覆盖
C/C++ 编译；cjc 编译 .cj 源、链接 step、cmake configure、install 拷贝
都不缓存，warm-cache 整体仍要约 12–17 分钟（compiler + tools 占大头）。

## 本机操作工具

`guide/` 是一个独立项目，用 `gh` CLI 在本机派发和监视这两个 Azure 工作流：

```bash
cd guide && uv sync
uv run cangjie-guide verbose      # 检查 gh、权限、工作流文件与 secret
uv run cangjie-guide build --dry-run
uv run cangjie-guide test --sdk latest --suites llt --changed-in HEAD
```

它有自己的 `pyproject.toml`、锁文件和检查配置，不参与 SDK 构建。详见 `guide/README.md`。

## 代码质量

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pyright           # strict 模式
uv run pytest
shellcheck .github/scripts/*.sh
shfmt -d .github/scripts
```

`build-cangjie.yml` 的 lint job 跑同样的检查。这些检查也可以挂到 git hook 上：

```bash
git config core.hooksPath .githooks
```

之后每次 `git commit` 都会依次跑上面这些命令。`shellcheck` 和 `shfmt` 不是项目
依赖，CI 单独安装；本机缺了它们，hook 会明确报缺失并让提交失败，不会静默跳过，
否则 hook 报的“通过”和 CI 的结果不是一回事。

Python 锁在
`>=3.11,<3.13`：cangjie 的 lldb 15 还在用 `PyEval_ThreadsInitialized`，
3.13 里被移除会编译失败。

## License

Apache-2.0（详见 `LICENSE`）。
