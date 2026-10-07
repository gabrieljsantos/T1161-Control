# MT500–T1161 Linux Graphics Tablet Driver

Driver experimental para as mesas comercializadas em marketplaces com estas
duas identificações completas:

- **ZINNIA MOMENTUM MT500** — código do produto **`ZNC-ZNMT500-BL01`**;
- **T1161** — código/modelo do produto **`T1161`**.

O dispositivo usa o identificador USB completo **VID `08f2` / PID `6811`**
(`08f2:6811`). A ZINNIA MOMENTUM MT500 também é reconhecida pelo sistema como
T1161. A interface de configuração foi desenvolvida para Linux com
GNOME/Wayland.

O projeto é específico para esses dois nomes de marketplace e para o hardware
T1161 correspondente.

> **Estado do projeto:** experimental. Antes de instalar, consulte a
> [matriz de compatibilidade](COMPATIBILITY.md).

## Apoie o projeto

<table>
  <tr>
    <td width="240" align="center" valign="middle">
      <img
        src="assets/pix-qrcode.svg"
        alt="QR Code para contribuição voluntária via Pix"
        width="220"
      >
    </td>
    <td valign="middle">
      <p>Se este driver foi útil, você pode contribuir voluntariamente via Pix.</p>
      <p>Escaneie o QR Code no aplicativo do seu banco e confirme os dados do recebedor antes de concluir.</p>
      <ul>
        <li><strong>Chave Pix (e-mail):</strong> <code>gabriel3d.com@gmail.com</code></li>
        <li><strong>Recebedor:</strong> Gabriel de Jesus Santos</li>
        <li><strong>Instituição:</strong> Nu Pagamentos S.A. – Instituição de Pagamento (Nubank)</li>
      </ul>
    </td>
  </tr>
</table>

## Funcionalidades

- entrada de caneta, pressão e distância por meio de dispositivos virtuais;
- configuração dos botões da caneta e da mesa;
- rolagem e gestos configuráveis;
- perfis de área, tela e rotação;
- painel rápido e configurador gráfico para GNOME/Wayland;
- telemetria e captura de diagnóstico para auxiliar novos testes.

Perfis com uma zona em 0° ou 180° funcionam no mecanismo atual. Perfis com
regiões independentes para várias telas podem ser salvos e editados, mas o
driver atual não executa esse mapeamento multizona.

## Compatibilidade confirmada

| Nome completo no marketplace | Código completo do produto | Identificação USB | Sistema testado | Estado |
|---|---|---|---|---|
| ZINNIA MOMENTUM MT500 | `ZNC-ZNMT500-BL01` | VID `08f2` / PID `6811` (`08f2:6811`); reconhecida como T1161 | Fedora Linux 44 Workstation, kernel `7.2.7-200.fc44.x86_64`, GNOME/Wayland, `x86_64` | Testada |
| T1161 | `T1161` | VID `08f2` / PID `6811` (`08f2:6811`) | Mesmo hardware/protocolo da ZINNIA MOMENTUM MT500 | Compatível |

O suporte é específico para essas duas identificações comerciais. Os detalhes do
teste estão em [COMPATIBILITY.md](COMPATIBILITY.md).

## Componentes

- `driver/`: driver USB em Rust e criação dos dispositivos virtuais;
- `t1161_configurator.py`: configuração gráfica;
- `t1161_control.py`: painel rápido e executor de ações;
- `t1161_profile_manager.py`: biblioteca e editor de perfis;
- `install.sh`: compila e atualiza a instalação local;
- `capture-t1161-usb.sh`: coleta dados para diagnóstico.

## Desenvolvimento e verificação

O driver requer Linux, acesso ao dispositivo USB, `uinput`, `systemd`, Rust/Cargo,
Python 3, GTK 4 e PyGObject. A interface atual foi criada para GNOME/Wayland.

Compile o componente Rust:

```bash
cargo build --release --manifest-path driver/Cargo.toml
```

Execute os testes da interface e dos perfis:

```bash
python3 -m unittest -v
```

Execute os testes do driver:

```bash
cargo test --manifest-path driver/Cargo.toml
```

O script `install.sh` pressupõe que o serviço de sistema
`t1161-driver.service` já existe e solicitará privilégios administrativos para
atualizar o binário. Revise o script antes de usá-lo em outra distribuição.

## Uso da interface

Para abrir o painel diretamente durante o desenvolvimento:

```bash
./t1161_control.py
```

Os perfis ficam em **Configurações → Perfis de área e telas**. O mapeamento
dos botões é salvo em `~/.config/t1161-control/buttons.json`, e o processo
**T1161 Actions** observa a telemetria para executar as ações configuradas.
