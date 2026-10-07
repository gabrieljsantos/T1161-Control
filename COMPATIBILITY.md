# Compatibilidade do MT500–T1161 Linux Graphics Tablet Driver

Este documento registra as duas identificações comerciais compatíveis encontradas
em marketplaces:

- **ZINNIA MOMENTUM MT500**, código completo **`ZNC-ZNMT500-BL01`**;
- **T1161**, código/modelo completo **`T1161`**.

Ambas correspondem ao identificador USB **VID `08f2` / PID `6811`**
(`08f2:6811`). A mesa ZINNIA também é reconhecida pelo sistema como T1161.

## Legenda

- **Testada:** funções principais verificadas no ambiente informado.
- **Compatível:** mesma plataforma T1161 e mesmo protocolo do hardware testado.

## Hardware

| Nome completo no marketplace | Código completo do produto | Nome detectado | USB (`VID:PID`) | Estado | Observações |
|---|---|---|---|---|---|
| ZINNIA MOMENTUM MT500 | `ZNC-ZNMT500-BL01` | T1161 | `08f2:6811` | Testada | Hardware usado no teste registrado |
| T1161 | T1161 | T1161 | `08f2:6811` | Compatível | Mesmo hardware/protocolo, vendido com a identificação T1161 |

## Sistemas operacionais testados

| Hardware | Distribuição | Versão | Kernel | Arquitetura | Sessão | Estado | Data do registro |
|---|---|---|---|---|---|---|---|
| ZINNIA MOMENTUM MT500 (`ZNC-ZNMT500-BL01`, T1161) | Fedora Linux Workstation | 44 | `7.2.7-200.fc44.x86_64` | `x86_64` | GNOME/Wayland | Testada | 2026-10-07 |

## Escopo do teste confirmado

No ambiente acima, o dispositivo foi reconhecido como T1161 e usado como alvo
do desenvolvimento do driver. O código atual seleciona explicitamente o USB
`08f2:6811`.

O registro **Testada** vale para o sistema operacional e o ambiente indicados na
tabela. A compatibilidade declarada limita-se aos nomes de marketplace ZINNIA
MOMENTUM MT500 e T1161; não há declaração de suporte a outros modelos.
