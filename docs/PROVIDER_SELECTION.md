# Provider 选择记录

## 当前选择

产线优先使用 Moli：

- 强模型：Anthropic Messages，`claude-opus-4-8`
- 弱模型：Chat Completions，`gpt-5.6-sol`
- 凭据：仅从仓库外的 `packy.env` 读取

最近一次同请求探测（2026-08-18）：Moli 强、弱端点均返回 HTTP 200；Packy 强、弱端点均返回 HTTP 401（无效令牌）。因此当前不使用 Packy。

## 切换原则

1. 切换前先运行安全探测，只输出 HTTP 状态、耗时和脱敏错误摘要，不输出 API key、请求头或完整响应。
2. 只有强模型和弱模型端点都返回成功，并且实际生成阶段的响应格式通过解析，才算渠道可用。
3. 渠道切换只对新阶段生效；已经生成的 task 不因渠道切换而改变作者、标答或测试的 provenance。
4. 任何 HTTP 200 但产物不符合路径、patch、测试或 QA 门槛的响应，归因于模型产物失败，不得标记为渠道可用的成功任务。

## 运行配置

真实配置不提交 Git。复制模板到仓库外并填写：

```bash
cp configs/providers.local.env.example ../packy.env
chmod 600 ../packy.env
```

探测和生产都使用同一个 env 文件；生产事件只记录 provider 域名、模型、HTTP 状态和 usage 摘要。
