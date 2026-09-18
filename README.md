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
