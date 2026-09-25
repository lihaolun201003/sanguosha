# Sanguosha

[English](#english) | [中文](#中文)

---

## English

A desktop *Sanguosha* game built with Python and Pygame.

This is a personal vibe-coding project. I started it as a small 1v1 prototype and have been gradually expanding the rules, cards, equipment, AI, and game flow.

### Features

- Desktop game based on Python + Pygame
- Basic cards: Sha, Shan, Tao, Jiu
- Equipment system
- Weapon and armor effects
- Trick cards
- Delayed trick cards
- Judgment area
- Complete turn phases
- Damage and dying flow
- AI opponents
- Chinese UI
- Card animations and interaction feedback

### Project Structure

```text
sanguosha/
├── main.py
├── src/
│   ├── game/
│   ├── card.py
│   ├── card_catalog.py
│   ├── deck.py
│   ├── player.py
│   ├── renderer.py
│   ├── actions.py
│   ├── response.py
│   └── start_menu.py
├── tests/
└── docs/
```

### Run

Python 3.9 is recommended.

Install Pygame:

```bash
pip install pygame
```

Run:

```bash
python3 main.py
```

> **Assets:** some game art (card faces, generals) is not distributed with this
> repository for licensing reasons — the game falls back to procedurally drawn
> graphics. To use the original art, place the image files under `assets/`
> yourself.

### Status

This project is still under development.

More game modes, characters, AI improvements, and additional game mechanics may be added later.

### About

This project was created mainly for fun and for practicing software development through vibe coding.

It is not affiliated with the official *Sanguosha* game or its publisher.

---

## 中文

这是一个使用 Python 和 Pygame 开发的桌面版《三国杀》项目。

这是我的个人 vibe coding 项目。最开始只是一个简单的 1v1 原型，后来逐渐加入了更多规则、卡牌、装备、AI 和完整的游戏流程。

### 已实现内容

- 基于 Python + Pygame 的桌面游戏
- 基本牌：杀、闪、桃、酒
- 装备系统
- 武器与防具效果
- 锦囊牌
- 延时锦囊牌
- 判定区
- 完整回合阶段
- 伤害与濒死流程
- AI 对手
- 中文界面
- 卡牌动画与交互反馈

### 项目结构

```text
sanguosha/
├── main.py
├── src/
│   ├── game/
│   ├── card.py
│   ├── card_catalog.py
│   ├── deck.py
│   ├── player.py
│   ├── renderer.py
│   ├── actions.py
│   ├── response.py
│   └── start_menu.py
├── tests/
└── docs/
```

### 运行方式

推荐使用 Python 3.9。

安装 Pygame：

```bash
pip install pygame
```

运行：

```bash
python3 main.py
```

> **素材说明：** 部分游戏素材（卡面、武将图）因授权原因不随本仓库分发，
> 缺少素材时游戏会退回程序绘制的图形。如需原始素材，请自行把图片放到
> `assets/` 目录下。

### 项目状态

项目目前仍在持续开发中。

后续可能继续加入更多游戏模式、武将、AI 改进以及更多游戏机制。

### 关于

这个项目主要是出于兴趣，以及通过 vibe coding 练习软件开发。

本项目与官方《三国杀》及其发行方无关联。

---

[Back to English](#english)
