from __future__ import annotations

from dataclasses import asdict, dataclass, field


LEGACY_PAD_COUNT = 12
PAD_COUNT = 24


@dataclass(slots=True)
class PadConfig:
    id: str
    label: str
    sound_path: str = ""
    sound_mode: str = "single"
    sound_pack_path: str = ""
    image_path: str = ""
    color_hex: str = "#3A4A63"
    volume_percent: int = 100
    hotkey: str = ""


@dataclass(slots=True)
class AppConfig:
    server_port: int = 8765
    selected_input_device_id: str = ""
    selected_input_device_name: str = ""
    selected_output_device_id: str = ""
    selected_output_device_name: str = ""
    monitor_muted: bool = False
    playback_volume_percent: int = 100
    mic_noise_gate_enabled: bool = True
    mic_noise_gate_threshold_db: float = -42.0
    raw_hd_soundboard_enabled: bool = False
    remote_playback_mode: str = "layer"
    remote_connection_mode: str = "local"
    pads: list[PadConfig] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def default_pads() -> list[PadConfig]:
    colors = [
        "#FF7043",
        "#29B6F6",
        "#66BB6A",
        "#AB47BC",
        "#FFA726",
        "#8D6E63",
        "#26C6DA",
        "#7E57C2",
        "#EC407A",
        "#EF5350",
        "#9CCC65",
        "#78909C",
        "#42A5F5",
        "#26A69A",
        "#D4E157",
        "#FFCA28",
        "#FF8A65",
        "#5C6BC0",
        "#7CB342",
        "#8E24AA",
        "#00ACC1",
        "#F4511E",
        "#6D4C41",
        "#546E7A",
    ]
    return [
        PadConfig(
            id=f"pad-{index + 1:02d}",
            label=f"Pad {index + 1}",
            sound_path="",
            image_path="",
            color_hex=colors[index],
            volume_percent=100,
        )
        for index in range(PAD_COUNT)
    ]
