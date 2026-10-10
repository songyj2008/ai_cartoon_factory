# 网页 LLM 文字任务后台执行：实施规格

日期：2026-10-02。基线：Chrome 扩展 0.4.19。
本文是后续编码任务的交付规格，本轮不修改运行代码。

## 1. 目标与边界

项目发起文字任务后，DeepSeek、ChatGPT、豆包的提交、续写、JSON 语法修复、回复读取、回传、会话恢复均自动执行。多个任务独立推进。普通操作不改变活动标签页；确有停滞证据时允许短暂唤醒。用户主动切换标签或窗口后，扩展不得切回去覆盖用户操作。

网页视频继续允许前台上传、输入、发送和确认。文字任务不得在视频的前台操作期间抢占焦点。

保留现有任务编号、lease、结果标记协议、并发配置、900 秒执行期限、手工应用修正版行为。自动读取完成不等于自动覆盖用户编辑稿。

“无感”的验收范围是正常登录、网页可用、浏览器正常运行时的任务执行。登录失效、验证码、额度不足进入明确的需处理状态；禁止不断切换标签或重新发请求。

## 2. 基线中必须修正的问题

1. worker.js 的 pump() await Promise.allSettled(operations)，poller.js 又等待整轮响应并用 busy 丢弃时钟事件。一个慢网页会延误其他任务下一次领取和读取。
2. processExisting() 的停滞唤醒仅针对 DeepSeek，并依赖中文 message 正则；ChatGPT 没有相同恢复能力。
3. invokePage() 的 1800ms 焦点恢复计时在 ensureBackgroundTab() 返回后才启动，前面的加载等待/异常不受保护。
4. invokePage()、ensureBackgroundTab()、trustedDoubaoSendClick()、videoPage() 各自处理激活和恢复，缺少统一互斥。
5. content.js persist() 和 worker.js saveBridgeTask() 整条写同一任务。迁移标签页后旧页面可能写回旧 tab_id；并发保存还可能覆盖新状态。
6. invoke() 的 Promise.race 超时不会取消底层网页操作。不能因为上层超时就释放发送权或重复执行。
7. recoverConversation() 在 processExisting() 的 try/catch 外；恢复失败缺少按任务的重试节奏、次数和诊断。
8. textOwnedTabs 整个对象读改写会丢失并发注册；Chrome 重启后数字 tab ID 也不能独立证明页面属于扩展。
9. report() 可能返回 timeout/cancelled，worker 却按本地 complete 保存已成功回传；必须采纳服务端返回的最终状态。

## 3. 文件范围

全部路径相对项目根目录 F:/work/comfyui/ai_cartoon_factory。

新增：

- tools/chrome_prompt_bridge/text_scheduler.js：按任务/标签页调度，轮询请求立即确认。
- tools/chrome_prompt_bridge/focus_coordinator.js：统一文字短暂唤醒和视频前台互斥。
- tools/chrome_prompt_bridge/text_task_store.js：后台文字任务唯一写入入口、绑定版本、专用页登记。
- tools/chrome_prompt_bridge/text_provider_policy.js：服务能力及会话地址识别的纯函数注册表。
- tools/chrome_prompt_bridge/tests/text_scheduler.test.cjs。
- tools/chrome_prompt_bridge/tests/focus_coordinator.test.cjs。
- tools/chrome_prompt_bridge/tests/text_task_store.test.cjs。
- tools/chrome_prompt_bridge/tests/text_provider_policy.test.cjs。

修改：

- tools/chrome_prompt_bridge/worker.js。
- tools/chrome_prompt_bridge/content.js。
- tools/chrome_prompt_bridge/poller.js。
- tools/chrome_prompt_bridge/video_worker.js：仅接入焦点协调器和非激活式页面就绪函数。
- tools/chrome_prompt_bridge/panel.js：同步脚本注入清单，并适配 pollBackend() 的快速确认响应，保持手工任务原流程。
- tools/chrome_prompt_bridge/tests/worker.test.cjs。
- tools/chrome_prompt_bridge/tests/bridge.test.cjs。
- tools/chrome_prompt_bridge/manifest.json：所有测试通过后升为 0.4.20。
- tools/chrome_prompt_bridge/README.md。

services/web_llm_bridge.py、ui/web_llm_routes.py 本轮原则上无需改接口。现有 diagnostics 数组、task 返回值和 execution_deadline_at 足够承载本次修复。不要再延长 TIMEOUT。services/text_job_operations.py、视频提示词编译、RunningHub/ComfyUI 提交代码不属于本任务。

## 4. 公共数据约定

后台任务仍存 bridgeTask:<id>，增加下列字段。保留已有字段名称及单位。

```js
{
  binding_epoch: 1,             // 每次替换绑定页面递增
  document_id: null,            // 注入成功后绑定 sender.documentId
  record_revision: 1,           // 后台每次接受 patch 后递增
  page_operation: {
    id: "uuid", kind: "send|read|repair|continue",
    phase: "started|acknowledged|uncertain|finished",
    started_at: 0
  },
  observation: {
    observed_at: 0,             // 最近一次成功检查，不代表内容有进展
    progress_at: 0,             // 答复内容、生成阶段实际变化时间
    conversation_id: "",
    reply_chars: 0,
    reply_hash: "",
    marker_start: false,
    marker_end: false,
    generating: false,
    reason_code: "WAITING_REPLY"
  },
  recovery: {
    next_attempt_at: 0,
    consecutive_failures: 0,
    wake_count: 0,
    last_wake_at: 0,
    last_error_code: ""
  },
  delivery: { state: "pending|accepted|late|rejected", backend_status: "" }
}
```

新增时间戳统一毫秒；已有 sent_at/created_at 的 ISO 字符串和 deadline_at 的 Unix 秒保持原约定，在边界函数转换。不得因读取、重试、唤醒重置总期限。

reason_code 固定枚举：WAITING_REPLY、REPLY_PROGRESS、COMPLETE_ENVELOPE、WRONG_CONVERSATION、PAGE_NOT_READY、PAGE_UNRESPONSIVE、LOGIN_REQUIRED、QUOTA_EXCEEDED、DRAFT_CONFLICT、STALE_BINDING、RESULT_PENDING_REPORT。

完成判断仍必须满足现有任务标记、request_id、结构校验以及至少 4 秒稳定。reply_hash 使用过滤用户消息后的答复文本计算，不能用页面全文的变化判断生成进展。

## 5. text_task_store.js 与 content.js 的保存改造

提供接口：create(task)、get(id)、patchFromPage(sender, payload)、patchFromWorker(id, patch)、rebind(id, binding)、registerOwnedTab(providerId, tab)、listOwnedTabs(providerId)。

每个任务一个短写锁，读取最新记录后合并并保存。写锁只保护 storage 读改写，不得跨网页调用、网络请求或等待页面，否则页面回传 patch 会和执行任务互相等待。

content.js 对 task.bridge 为真的任务，不再直接 chrome.storage.local.set 整条任务。persist(next) 改为发送 AICF_TASK_PATCH：taskId、bindingEpoch、operationId（有则带上）、patch。后台校验后返回权威 task；页面用它替换自己的 task。手工侧边栏 task 不走这一通道，保留已有存储方式。

patchFromPage 必须校验 sender.id、frameId===0、sender.tab.id、documentId、provider、binding_epoch。拒绝旧页面和旧绑定写入。页面允许更新状态、请求发送证据、回复、observation、续写/修复信息；不允许改 tab_id、bridge、lease、deadline_at、binding_epoch、delivery。

页面报告 conversation_url/conversation_id 应作为候选绑定提交，后台按同服务地址及页面任务证据确认，不能直接信任任意 URL。

发送前 persist(sending/dispatch_attempted_at) 必须得到后台确认，才允许实际点击。后台不可达时停止新的发送动作。只读结果保留在页面内，后续重新回传。

complete/cancelled/error/timeout 不允许被旧 waiting patch 覆盖。重复提交相同完成结果应幂等。

worker 的 pageFlights/任务操作锁不能阻塞 AICF_TASK_PATCH 处理。页面等待 patch 确认时，后台必须能立即处理消息。

专用页登记使用每页独立 key，避免整体对象覆盖。运行期所有权放 chrome.storage.session；Chrome 会话结束后不得仅凭 local 中旧 tab ID 操作用户页面。local 只保存任务对话地址等恢复信息。扩展更新导致 session 登记不可用时，保守创建新的后台页恢复读取，不认领用户已有页。

## 6. text_scheduler.js、worker.js 和 poller.js

调度器提供 requestTick(reason)、scheduleTask(taskId, tabId, run)、scheduleDiscovery(run)、scheduleVideo(run)。

requestTick 仅记录需要调度并启动一次异步检查；相同类型触发合并。任务操作可以并行，任务 ID 和 tab ID 分别互斥。discovery 的 /ping、建池、/claim 与已有任务结果读取分开锁，不能让一次 10 秒 /ping 超时阻塞完成结果回传。

worker.js 的 AICF_BACKEND_POLL handler 同步执行 requestTick('clock') 后立即 respond({ok:true,accepted:true})。不 await pump()、videoPumpFromStorage()、storage 全量扫描或网页响应。结果状态通过已有 bridgeStatus 存储提供，不能把心跳应答当作任务已完成。

panel.js 的 pollBackend() 收到 accepted 后只表示已请求检查；读取当前 bridgeStatus 展示缓存状态，并继续使用已经存在的 chrome.storage.onChanged 监听显示后续状态，不能显示“任务成功”。worker 新模块使用 importScripts 加载：protocol.js、text_provider_policy.js、text_task_store.js、text_scheduler.js、focus_coordinator.js、video_worker.js。模块导出明确命名空间，并支持 Node 测试导出；不要引入构建工具。

poller.js 的 busy 仅覆盖这次快速应答；为发送心跳加最多 2 秒的等待看门狗，之后允许下个 tick。看门狗超时不代表页面操作已取消，也不触发任务重发。继续使用现有 1 秒 clock 和 30 秒 alarm，不再增加保活循环。

worker.js 中删除 pump() 的整轮 await Promise.allSettled(operations)。每个操作通过调度器登记 Promise，各自 catch/finally；任务 A 未完成时仍能安排任务 B。完成后 requestTick('operation-finished')，但发现没有队列/到期操作时不得立即无限空转。

默认读取节奏 2 秒/任务；结果就绪通知立即安排回传；失败读取退避 2、5、10、30 秒。每轮公平选择到期任务，避免一个失败任务一直排在队首。

保留按服务/总数并发上限，后端 claim 仍是容量和 lease 的最终权威。发送前先持久化 page_operation；service worker 重启后根据持久记录进入只读恢复，不能认为内存 Map 消失就可以重发。

invoke() 的超时只结束调用者等待，不清除底层实际 flight。超时发送标记 uncertain。底层未结束时禁止同页另一次 send/resume；worker 重启后查询页面 AICF_OPERATION_STATUS，不能根据锁超时自行判断请求没发出。页面操作状态只回答当前 taskId/operationId，不触发点击。

正常已提交的 waiting 任务使用 AICF_READ。只有确认从未开始 dispatch 的 prepared 任务才允许走发送流程。续写和语法修复保留独立操作编号及现有次数上限。

## 7. text_provider_policy.js 与停滞判定

注册表字段：id、displayName、homeUrl、matchesUrl(url)、conversationId(url)、isCanonicalConversation(url)、allowReadWake、allowSendWake、stallMs、wakeCooldownMs、maxWakes。

DeepSeek、ChatGPT、豆包共用默认恢复参数：stallMs=45000、wakeCooldownMs=90000、maxWakes=3。这些是确定的初始策略值，不声称是网站保证值。

ChatGPT 支持 /c/<id> 及 /g/<...>/c/<id>；DeepSeek 支持 /a/chat/s/<id>；豆包支持 /chat/<id>，local_ 开头属于临时地址，不能作为重新打开页面的正式恢复地址。

content.js 的会话地址识别调用策略注册表纯函数。worker 的 PROVIDER_NAMES/URLS、DeepSeek 专用中文正则判断改为读取注册表。content 脚本注入顺序为 protocol.js、text_provider_policy.js、content.js，worker 和 panel 的注入清单一起更新。

content.js check() 返回结构化 observation。只在回复内容 hash 或生成阶段改变时更新 progress_at，普通检查只更新 observed_at。未出现答复时从已确认发送时间计算停滞。

需要唤醒必须同时满足：任务未终止、无完整结果待回传、连续 45 秒没有内容进展、冷却期已过、唤醒次数未达上限、无登录/额度/草稿冲突、当前没有视频前台操作。生成中的答复持续增长时不唤醒。

唤醒结束立即只读检查；不得使用 AICF_RESUME 借机再次发送。没有进展继续等待到原总期限；禁止以 reload、新会话或再次 send 作为通用停滞恢复。

## 8. focus_coordinator.js

接口：withTextWake({taskId,tabId,reason}, action)、withVideoForeground({taskId,tabId}, action)、recoverInterruptedLease()。

所有激活和恢复集中到此模块。worker 的 ensureBackgroundTab() 改成 ensureTabReady()，只负责 autoDiscardable、加载和允许的只读恢复，不激活标签页。

文字唤醒流程：获取焦点租约→记录 windowId、originalTabId、目标页、截止时间→在激活前启动 1800ms 恢复计时→激活→等待最多 400ms 让页面恢复运行→执行短操作/只读检查→finally 恢复。恢复计时不等待 action 结束，不能 await 30 秒加载后才开始计时。

文字焦点租约同一时刻最多一个。浏览器不在前台、视频租约持有中、用户已切去别的窗口时，返回 deferred，任务仍等待，不判失败。不调用 chrome.windows.update({focused:true})。

监听 tabs.onActivated 和 windows.onFocusChanged。自己的激活/恢复使用预期目标标记识别；任何非预期激活事件将租约标为 userInterrupted。恢复必须同时满足：目标页仍活动、窗口仍为原窗口、未发生用户干预、原标签仍存在。用户主动切走后不恢复。

租约存 chrome.storage.session，worker 被终止后下次唤醒检查过期租约。只有归属与当前焦点证据仍充分时才恢复，否则清除租约，不猜测用户意图。1800ms 是正常运行下的最大持有目标，不能承诺操作系统挂起时仍精确执行。

文字租约到时恢复焦点，并不代表页面操作已取消；操作锁仍由调度器管理。禁止因此重新发送。

视频前台租约不套用文字的 1800ms 限制。videoPage() 的 SUBMIT/CONFIRM/DOWNLOAD 在前台租约中执行；READ 不申请前台租约。视频等待后台生成期间释放租约。视频失败可保持当前页面供用户查看，但必须释放租约；恢复是否执行沿用原有 completed 规则。

trustedDoubaoSendClick() 的 activateIfNeeded 分支进入同一个文字租约；同 taskId/tabId 已持有租约时复用，不二次排队造成死锁。其他真实点击通过当前操作上下文判断是否需要租约，不新增页面无条件激活。

## 9. recoverConversation() 的明确算法

第一步读取最新权威任务；已完成先安排回传。获取任务/当前页面操作锁。

当前页面同一 conversationId（忽略 query/hash 差异）则继续读；地址变化但页面有本次已提交请求和合法任务证据时允许更新绑定。

需要恢复时，目标必须是策略认定的正式同服务会话地址。首页、豆包 local_ 临时地址不能用于恢复。没有正式地址则保持只读等待并记录原因，不新建请求。

原页属于扩展、没有其他草稿且没有未结束的输入操作时，允许导航回正式会话。原页已关闭、属于用户、或存在其他草稿时，新建 active:false 的恢复页，保留原页内容。

迁移步骤：为新页申请操作锁→在后台 store 中 rebind 并递增 binding_epoch→注入并绑定 documentId→只读检查→释放旧绑定。每一步持久化，旧 epoch 的 patch 全部拒绝。

恢复放入 processExisting() 的受控异常处理内。失败按 2/5/10/30 秒退避，连续 3 次页面恢复失败后停止建页/导航，继续有限只读检查直到总期限，记录 PAGE_NOT_READY/PAGE_UNRESPONSIVE。不要每秒反复创建标签。

重新打开正式会话只恢复读取；发送是否成功不明确时仍不得重发。已经有尚未完成的 JSON 修复/续写操作时也遵守同一限制。

## 10. 结果回传和服务端状态

本地 complete 必须持久保存 result/raw，delivery=pending。网络失败按 2/5/10/30 秒重试。不能由于一次 HTTP 失败丢掉结果或把网页任务改成生成失败。

worker 使用 POST 返回的 task.status：complete → delivery=accepted；timeout/cancelled/error → 记录该服务端状态，delivery=late/rejected。只有服务端接受结果或确认已保留迟到记录后，才清理大字段。

后端已 cancelled 的任务不恢复执行。后端 timeout 的迟到结果沿用现有 late_results 语义，不自动覆盖编辑器；不能把本地 complete 显示为服务端成功。

AICF_RESULT_READY 或完成 patch 只唤醒这个任务的回传，不必等待全池扫描；后台重新启动后扫描本地 complete 且未确认回传的记录补报。

现有 report() diagnostics 是数组，可追加 {at,event,task_id,tab_id,binding_epoch,reason_code,...}，沿用 200 条/单条大小限制。记录状态变化、恢复、唤醒、回传错误，不每秒重复追加相同消息。

## 11. 实施顺序及禁止返工项

1. 新增策略注册表及 store，接通 AICF_TASK_PATCH、binding_epoch，保证旧页面不能回写新绑定。
2. 新增调度器；修改 worker/poller 快速确认和任务独立调度。保留底层未结束操作的互斥。
3. 新增焦点协调器；迁移文字激活入口，再接入 videoPage。不要先删旧恢复代码再留空入口。
4. content 输出 observation，替换 DeepSeek 中文正则，实现三家统一停滞策略。
5. 重写 recoverConversation 的错误处理、正式地址判断、草稿保护和绑定迁移。
6. 修正回传确认及重试，最后更新版本和 README。

每一步只跑受影响测试；出现失败先区分测试模拟不完整与真实行为回归。禁止为了通过旧测试恢复占用用户标签的行为。禁止只把 await 删除而不补持久状态、互斥和异常处理。禁止再用延长总超时掩盖卡住。禁止增加每秒激活页面的循环。

新增渠道时，本次新增的调度/store/焦点模块不应增加服务名称分支；服务差异通过 policy 注册。网站具体 DOM 适配仍有现有历史分支，本轮不宣称已重构整个项目为完全插件化。

## 12. 必须通过的回归用例

- 任务 A 的 sendMessage 永不返回；任务 B 仍可领取、读取并回传；时钟应答不等待 A。
- 一个 /ping 延迟 10 秒，已完成任务的回传仍可执行。
- 同一任务连续收到 10 次 tick/结果通知，不出现重叠发送；页面响应超时后底层 flight 仍在时不重发。
- worker 重建且内存锁消失，已开始 dispatch 的任务进入只读恢复。
- ChatGPT、DeepSeek、豆包均可按结构化停滞证据短暂唤醒；持续增长的回答不唤醒；冷却/次数限制有效。
- 唤醒后加载超时、注入失败、消息无响应，正常运行的定时器仍恢复原标签；用户主动切换后不恢复。
- 两个文字任务同时请求焦点，只允许一个租约；视频上传期间文字唤醒 deferred，视频后台生成期间文字可正常读。
- 从用户页恢复任务时新建后台页；专用页含其他草稿时也不覆盖、不导航。
- 迁移后旧 document/旧 binding_epoch 的 waiting/complete patch 被拒绝；新绑定能够完成。
- 两个任务同时登记新页，登记均保留；Chrome 新会话中旧数字 tab ID 不被当作所有权证据。
- 正式会话地址 query/hash 变化不导航；豆包 local_ 地址不触发重新打开。
- complete 回传网络失败保留结果并重试；POST 返回 timeout/cancelled 时不显示 accepted。
- 900 秒期限前后行为一致；排队不计入执行时间；唤醒/修复不重置期限。
- 手工侧边栏任务仍可填入、发送、读取；网页视频上传/提示词/发送/确认/下载原有行为通过回归。

运行命令：

```powershell
node --test tools/chrome_prompt_bridge/tests/*.test.cjs
E:\tools\anaconda\envs\ai_cartoon_factory\python.exe -m pytest tests/test_web_llm_bridge.py -q
```

真实浏览器验收：重载扩展与项目后，用户保持在项目页面或其他应用，同时发起两项文字任务，其中一项 ChatGPT；两项结果都应自动回传。再执行一次网页视频提交与文字任务并行，确认焦点没有互相争抢。验收记录必须注明是否实际完成；模拟测试不能替代这一步。避免为验证重复提交同一个生成任务。

## 13. 生命周期依据

Chrome service worker 随时可能被回收，Promise 和全局 Map 不可作为唯一状态。继续使用既有 offscreen 时钟和 alarm 提供触发，任务恢复依赖持久记录；不以长期保活替代恢复设计。

官方依据：https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle
