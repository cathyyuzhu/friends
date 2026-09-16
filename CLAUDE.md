# CLAUDE.md

本项目的规范文件说明本项目做什么、给谁用、以及怎么实现的，统一放在 `spec/` 文件夹下：

- [spec/mission.md](spec/mission.md) —— 为什么做这个项目，目标用户，成功的样子
- [spec/spec.md](spec/spec.md) —— 功能需求、验收标准、已知限制
- [spec/techsolution.md](spec/techsolution.md) —— 技术栈、架构、关键设计决策
- [spec/milestone.md](spec/milestone.md) —— 推进顺序，已完成/进行中/待办
- [spec/realism_upgrade.md](spec/realism_upgrade.md) —— **当前进行中的改造任务**：消除"AI 味"的具体实现说明，动手前必读

开始任何改动前，先看一眼上面三个文件，确保改动方向和已有决策一致。若改动会影响这些文件描述的内容（新功能、架构调整、目标变化），同步更新对应文件。

## 工作流约定

- 改完代码后，如果有正在跑的进程依赖这份代码（比如后台跑着的 `telegram_bot.py`），要立即重启它让改动生效，不用等用户开口要求重启。
