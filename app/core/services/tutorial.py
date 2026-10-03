"""Tutorial model and state (Qt-free).

`TutorialStep` describes one step declaratively; the UI resolves `target` keys to real widgets. New steps are added
to `TUTORIAL_STEPS` only — nothing else in the interface needs to change.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from app.infrastructure.database import Database
from app.utils.formatters import now_iso


class Placement(str, Enum):
    CENTER = "center"
    BOTTOM = "bottom"
    TOP = "top"
    RIGHT = "right"
    LEFT = "left"


@dataclass(frozen=True)
class TutorialStep:
    id: str
    title: str
    description: str
    order: int
    target: str | None = None          # key registered by the UI; None = centered card without spotlight
    placement: Placement = Placement.BOTTOM
    page: str | None = None            # page to show before highlighting
    icon: str = "info"                 # Lucide icon name
    skippable: bool = True
    demo: bool = False                 # show clearly-labelled sample data while this step is visible
    action: str | None = None          # optional extra action id (handled by the UI)
    action_label: str | None = None


TUTORIAL_STEPS: tuple[TutorialStep, ...] = (
    TutorialStep(
        "welcome", "Bem-vindo ao Luut Video Downloader",
        "Baixe vídeos de URLs compatíveis, organize sua fila e acompanhe tudo em um único lugar.<br><br>"
        "Neste pequeno tutorial você verá: URL, análise, qualidade, download, fila, histórico, widget, atalhos e "
        "configurações — direto na própria interface.", 10, icon="sparkles", placement=Placement.CENTER),
    TutorialStep(
        "sidebar", "Navegação",
        "<b>Início</b> — comece um novo download e veja um resumo.<br>"
        "<b>Downloads</b> — downloads atuais e a fila.<br>"
        "<b>Histórico</b> — downloads anteriores.<br>"
        "<b>Configurações</b> — personalize o comportamento do aplicativo.<br>"
        "<b>Sobre</b> — informações sobre o aplicativo.", 20,
        target="sidebar", placement=Placement.RIGHT, page="home", icon="layers"),
    TutorialStep(
        "url", "Link do vídeo",
        "É aqui que você cola o link do vídeo. Você também pode usar <b>Ctrl+V</b>, o botão <b>Colar</b> "
        "ou arrastar o link para a janela.", 30, target="url", page="home", icon="link"),
    TutorialStep(
        "analyze", "Analisar",
        "O aplicativo analisa o conteúdo e identifica as informações e os formatos disponíveis. "
        "Nada é baixado nesta etapa.", 40, target="analyze", page="home", icon="search"),
    TutorialStep(
        "info", "Informações do vídeo",
        "Depois da análise, você visualiza título, duração, thumbnail, qualidade máxima, tamanho estimado e "
        "formatos disponíveis. <i>Os dados mostrados agora são apenas um exemplo.</i>", 50,
        target="info", page="home", icon="film", demo=True),
    TutorialStep(
        "quality_format", "Qualidade e formato",
        "Quando houver opções, escolha a qualidade (só aparecem as realmente disponíveis) e o formato: "
        "<b>MP4</b>, <b>formato original</b> ou <b>Somente áudio (MP3)</b>.", 60,
        target="quality_format", page="home", icon="settings", demo=True),
    TutorialStep(
        "destination", "Pasta de destino",
        "Escolha onde deseja salvar seus arquivos. A última pasta usada é lembrada, e a pasta padrão pode ser "
        "definida agora ou em Configurações.", 70, target="destination", page="home", icon="folder", demo=True,
        action="choose_default_dir", action_label="Escolher pasta padrão"),
    TutorialStep(
        "queue", "Adicionar à fila",
        "Você não precisa baixar um vídeo por vez: adicione quantos quiser à fila. Não existe limite "
        "artificial para o total da fila — apenas a quantidade de downloads <b>simultâneos</b> é controlada.", 80,
        target="queue", page="home", icon="list-plus", demo=True),
    TutorialStep(
        "downloads", "Acompanhe seus downloads",
        "Aqui você acompanha progresso, velocidade, tamanho e tempo estimado, e pode pausar, retomar, "
        "cancelar ou reordenar a fila. <i>O card destacado é um exemplo e não é um download real.</i>", 90,
        target="downloads", page="downloads", icon="download", demo=True),
    TutorialStep(
        "history", "Histórico",
        "Os downloads concluídos ficam registrados para você localizar o arquivo, abrir a pasta ou baixar "
        "novamente. Excluir um registro não apaga o vídeo.", 100,
        target="history", page="history", icon="history"),
    TutorialStep(
        "settings", "Configurações",
        "Defina pasta padrão, downloads simultâneos, notificações, histórico, organização automática e outras "
        "preferências. O tutorial pode ser reiniciado em <b>Ajuda / Tutorial</b>.", 110,
        target="settings", page="settings", icon="settings"),
    TutorialStep(
        "recovery", "Recuperação automática",
        "O aplicativo salva o estado dos downloads. Se ele fechar inesperadamente, a fila é recuperada na "
        "próxima abertura. Enquanto baixa, o arquivo fica como <b>.part</b> (incompleto) na pasta temporária "
        "e só vira o arquivo final quando o download termina de verdade — assim é possível retomar de onde "
        "parou.", 120, target="nav_downloads", placement=Placement.RIGHT, page="downloads", icon="life-buoy"),
    TutorialStep(
        "widget", "Widget flutuante",
        "Uma janelinha sempre à mão para colar um link e baixar sem abrir a janela principal. Ele vem "
        "<b>desativado</b>: ative em <b>Configurações → Widget</b>. Widget e aplicativo usam a mesma fila.", 124,
        target="settings_widget", page="settings:widget", icon="window"),
    TutorialStep(
        "shortcuts", "Atalhos",
        "Controle o Luut pelo teclado. Em <b>Configurações → Atalhos</b> você personaliza cada comando, separados "
        "entre aplicação desktop, widget, downloads e sistema. Atalhos <b>GLOBAIS</b> funcionam mesmo com o Luut "
        "em segundo plano.", 126, target="settings_shortcuts", page="settings:shortcuts", icon="keyboard"),
    TutorialStep(
        "done", "Tudo pronto!",
        "Agora você já sabe como utilizar o Luut Video Downloader.", 130,
        icon="party-popper", placement=Placement.CENTER, skippable=False),
)


# Short tour of the floating widget, offered the first time the widget is enabled. Targets are widget parts.
WIDGET_TOUR_STEPS: tuple[TutorialStep, ...] = (
    TutorialStep("w_intro", "Conheça o Widget do Luut",
                 "Esse é o Widget do Luut. Ele permite iniciar downloads sem abrir a janela principal.", 10,
                 target="panel", icon="window"),
    TutorialStep("w_url", "Link do vídeo", "Cole aqui o endereço do vídeo — ou use o botão <b>Colar</b>.", 20,
                 target="url", icon="link"),
    TutorialStep("w_analyze", "Analisar", "O Luut consulta o link e mostra as opções realmente disponíveis. "
                 "Nada é baixado nesta etapa.", 30, target="analyze", icon="search"),
    TutorialStep("w_options", "Qualidade e formato", "Depois da análise, escolha a qualidade, o tipo (vídeo + áudio, "
                 "vídeo ou áudio) e o formato. Só aparecem opções que a fonte realmente oferece.", 40,
                 target="url", icon="settings"),
    TutorialStep("w_start", "Iniciar download", "<b>INICIAR DOWNLOAD</b> envia o vídeo para a mesma fila do "
                 "aplicativo principal. Você pode continuar navegando enquanto ele baixa.", 50,
                 target="analyze", icon="download"),
    TutorialStep("w_queue", "Fila", "O botão de fila mostra os downloads, com pausar, retomar, cancelar, abrir "
                 "arquivo e abrir pasta.", 60, target="queue", icon="list-plus"),
    TutorialStep("w_shortcuts", "Atalhos", "Você pode configurar todos os atalhos em <b>Configurações → "
                 "Atalhos</b>. Por padrão, <b>Ctrl + Shift + L</b> mostra/oculta o widget de qualquer lugar.", 70,
                 target="panel", icon="keyboard"),
    TutorialStep("w_clipboard", "URLs copiadas", "Opcionalmente, o Luut pode detectar URLs copiadas "
                 "(Configurações → Widget).<br><br><b>O Luut nunca inicia um download automaticamente apenas "
                 "porque uma URL foi copiada.</b>", 80, target="panel", icon="clipboard", skippable=False),
)

TUTORIAL_MAIN = "main"
TUTORIAL_WIDGET = "widget"
TUTORIAL_SHORTCUTS_INTRO = "shortcuts"


def ordered_steps(steps: tuple[TutorialStep, ...] = TUTORIAL_STEPS) -> list[TutorialStep]:
    return sorted(steps, key=lambda s: s.order)


@dataclass(frozen=True)
class TutorialState:
    completed: bool = False
    dont_show: bool = False
    last_step: str | None = None
    completed_at: str | None = None

    @property
    def should_autostart(self) -> bool:
        return not self.completed and not self.dont_show


class TutorialStateRepository:
    """One row per guide: "main" (first-run tutorial), "widget" (widget tour), "shortcuts" (Atalhos intro)."""

    def __init__(self, db: Database, key: str = TUTORIAL_MAIN) -> None:
        self._db = db
        self._key = key

    def for_key(self, key: str) -> TutorialStateRepository:
        return TutorialStateRepository(self._db, key)

    def load(self) -> TutorialState:
        row = self._db.query_one("SELECT * FROM tutorial_state WHERE id = ?", (self._key,))
        if row is None:
            return TutorialState()
        return TutorialState(bool(row["completed"]), bool(row["dont_show"]), row["last_step"], row["completed_at"])

    def save(self, state: TutorialState) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO tutorial_state (id, completed, dont_show, last_step, completed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (self._key, int(state.completed), int(state.dont_show), state.last_step, state.completed_at))

    def update(self, **changes: object) -> TutorialState:
        state = replace(self.load(), **changes)
        self.save(state)
        return state

    def mark_completed(self) -> TutorialState:
        return self.update(completed=True, completed_at=now_iso(), last_step="done")

    def reactivate(self) -> TutorialState:
        """Show the tutorial again on the next launch."""
        return self.update(completed=False, dont_show=False, last_step=None)


class TutorialManager:
    """Step navigation + persistence. The UI observes it; it never touches widgets."""

    def __init__(self, repository: TutorialStateRepository, steps: tuple[TutorialStep, ...] = TUTORIAL_STEPS) -> None:
        self._repo = repository
        self.steps = ordered_steps(steps)
        self.index = 0
        self.running = False

    @property
    def state(self) -> TutorialState:
        return self._repo.load()

    @property
    def current(self) -> TutorialStep:
        return self.steps[self.index]

    @property
    def is_first(self) -> bool:
        return self.index == 0

    @property
    def is_last(self) -> bool:
        return self.index == len(self.steps) - 1

    def should_autostart(self) -> bool:
        return self.state.should_autostart

    def start(self) -> TutorialStep:
        self.running = True
        self.index = 0
        return self.current

    def next(self) -> TutorialStep | None:
        if self.is_last:
            self.finish(completed=True)
            return None
        self.index += 1
        self._repo.update(last_step=self.current.id)
        return self.current

    def back(self) -> TutorialStep:
        self.index = max(0, self.index - 1)
        return self.current

    def set_dont_show(self, value: bool) -> None:
        self._repo.update(dont_show=value)

    def reactivate(self) -> None:
        self._repo.reactivate()

    def finish(self, completed: bool) -> None:
        """completed=False means skipped/closed: it starts again next launch unless 'don't show' is checked."""
        self.running = False
        if completed:
            self._repo.mark_completed()
        else:
            self._repo.update(last_step=self.current.id)
