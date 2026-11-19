import ctypes
import csv
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from threading import Event, Lock, Thread
from time import monotonic
import traceback
from uuid import uuid4

import adsk.core
import adsk.fusion

from .quote_bridge import ACTIVITY_URL, MAX_FILES, QuoteBridge


COMMAND_ID = 'Initial_FusionQuanzhouQuote'
QUICK_ID = COMMAND_ID + '_Quick'
QUICK_MENU_ID = COMMAND_ID + '_QuickMenu'
EXPORT_EVENT_ID = COMMAND_ID + '_Export'
PANEL_ID = 'SolidScriptsAddinsPanel'
TITLE = '铨洲 6.8 活动报价'
SELECTION_ID = 'quanzhou_part'
AUTO_ID = 'quanzhou_confirm_6061'
FOLDER_ID = 'quanzhou_export_folder'
FOLDER_ACTIONS_ID = 'quanzhou_export_folder_actions'
BROWSE_FOLDER = '选择文件夹…'
RESET_FOLDER = '恢复默认位置'
FILTERS = ('Occurrences', 'RootComponents', 'SolidBodies', 'SolidFaces')

_app = None
_ui = None
_bridge = None
_export_event = None
_definition = None
_control = None
_quick_definition = None
_quick_menu_definition = None
_quick_control = None
_quick_start_handler = None
_toolbar_selection = None
_menu_selection = ()
_menu_handler = None
_handlers = []
_pending_export = None
_browser_launch_lock = Lock()
_last_browser_launch = -float('inf')


class QuoteError(Exception):
    pass


@dataclass
class _ExportRequest:
    directory: Path
    auto_confirm: bool
    files: list = field(default_factory=list)
    remaining: list = field(default_factory=list)
    token: str = field(default_factory=lambda: uuid4().hex)
    done: Event = field(default_factory=Event)


def _describe_error(error):
    if isinstance(error, QuoteError):
        return str(error)
    _app.log(traceback.format_exc())
    return f'铨洲报价操作失败。\n{error}'


def _selection_types(entity):
    return (
        adsk.fusion.Occurrence.cast(entity),
        adsk.fusion.Component.cast(entity),
        adsk.fusion.BRepBody.cast(entity),
        adsk.fusion.BRepFace.cast(entity),
    )


def _single_solid(component):
    solids = [body for body in component.bRepBodies if body.isSolid]
    return solids[0] if len(solids) == 1 else None


def _standalone_body():
    design = adsk.fusion.Design.cast(_app.activeProduct)
    if not design or not design.isValid:
        return None
    root = design.rootComponent
    if not root or not root.isValid or root.occurrences.count:
        return None
    return _single_solid(root)


def _resolve_part(entity):
    occurrence, component, body, face = _selection_types(entity)
    if occurrence:
        if not occurrence.isValid:
            raise QuoteError('所选组件实例已失效，请重新选择。')
        component = occurrence.component
        if not component:
            raise QuoteError('该组件不可用，请选中另一个零件。')
    if component:
        if not component.isValid:
            raise QuoteError('该组件不可用，请选中另一个零件。')
        if component.occurrences.count or (occurrence and occurrence.childOccurrences.count):
            raise QuoteError('请展开装配，选中要报价的零件组件，或选中它的实体。')
        solid = _single_solid(component)
        if solid is None:
            raise QuoteError('请选择含一个实体的组件，或展开实体文件夹选中要报价的实体。')
        return solid, component.name
    if face:
        body = face.body
    if not body or not body.isSolid:
        raise QuoteError('请选择一个实体、实体上的一个面，或只含一个实体的组件。')
    native_body = body.nativeObject or body
    name = native_body.name
    component = native_body.parentComponent
    if component and sum(part.isSolid for part in component.bRepBodies) == 1:
        name = component.name
    return native_body, name


def _data_folder():
    app_data = Path(os.environ.get('LOCALAPPDATA') or Path.home())
    return app_data / 'FusionQuanzhouQuote'


def _default_export_folder():
    return _data_folder() / 'exports'


def _load_export_folder():
    try:
        settings = json.loads((_data_folder() / 'settings.json').read_text(encoding='utf-8'))
        value = settings.get('export_folder') if isinstance(settings, dict) else None
        if isinstance(value, str) and '\x00' not in value:
            folder = Path(value)
            if folder.is_absolute():
                return folder
    except (OSError, ValueError):
        pass
    return _default_export_folder()


def _save_export_folder(folder):
    directory = _data_folder()
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f'.settings-{uuid4().hex}.tmp'
    try:
        temporary.write_text(json.dumps({'export_folder': str(folder)}, ensure_ascii=False) + '\n', encoding='utf-8')
        temporary.replace(directory / 'settings.json')
    finally:
        temporary.unlink(missing_ok=True)


def _file_stem(name):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:90].rstrip(' .')
    if not stem:
        stem = 'Part'
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]', stem.split('.')[0], re.I):
        stem = '_' + stem
    return stem


def _batch_directory(folder):
    prefix = datetime.now().strftime('%Y%m%d-%H%M%S-')
    try:
        folder.mkdir(parents=True, exist_ok=True)
        return Path(mkdtemp(prefix=prefix, dir=folder))
    except OSError as error:
        raise OSError(f'无法在当前保存位置创建 STEP 导出文件夹。请在报价窗口选择其他文件夹。\n{folder}\n{error}') from error


def _write_step(design, output, component=None):
    manager = design.exportManager
    if component is None:
        options = manager.createSTEPExportOptions(str(output))
    else:
        options = manager.createSTEPExportOptions(str(output), component)
    if not options or not manager.execute(options):
        raise QuoteError('Fusion 的 STEP 导出失败，请检查零件几何。')
    if not output.is_file():
        raise QuoteError('Fusion 没有生成 STEP 文件。')
    with output.open('rb') as exported:
        if not exported.read(64).lstrip().startswith(b'ISO-10303-21;'):
            raise QuoteError('导出的文件缺少有效的 STEP 文件头。')


def _direct_component(solid):
    component = solid.parentComponent
    if (component and component.isValid and component.bRepBodies.count == 1
            and component.occurrences.count == 0 and component.meshBodies.count == 0):
        return component
    return None


def _export_solid(solid, output):
    # Use the native body in its component's coordinate system. The source document
    # stays open while the STEP translator works on an isolated, unsaved document.
    temporary = adsk.fusion.TemporaryBRepManager.get().copy(solid)
    if not temporary or not temporary.isSolid:
        raise QuoteError('Fusion 无法复制所选实体。')

    document = _app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
    if not document:
        raise QuoteError('Fusion 无法创建用于导出 STEP 的临时文档。')
    try:
        design = adsk.fusion.Design.cast(document.products.itemByProductType('DesignProductType'))
        if not design:
            raise QuoteError('Fusion 无法读取临时设计。')
        design.designType = adsk.fusion.DesignTypes.DirectDesignType
        copy = design.rootComponent.bRepBodies.add(temporary)
        if not copy or not copy.isSolid:
            raise QuoteError('Fusion 无法将所选实体写入临时设计。')
        copy.name = output.stem
        _write_step(design, output)
    finally:
        if not document.close(False):
            _app.log('FusionQuanzhouQuote: could not close temporary export document.')


def _browser_running():
    tasklist = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'tasklist.exe'
    try:
        result = subprocess.run([str(tasklist), '/FO', 'CSV', '/NH'], capture_output=True,
                                check=True, text=True, errors='replace', timeout=5,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return True
    return any(row and row[0].lower() in {'msedge.exe', 'chrome.exe'}
               for row in csv.reader(result.stdout.splitlines()))


def _watch_browser(bridge, received, directory):
    global _last_browser_launch
    if received.wait(1.5) or bridge.closed:
        return
    with _browser_launch_lock:
        if monotonic() - _last_browser_launch >= 15 and not _browser_running():
            if not received.is_set() and not bridge.closed:
                _last_browser_launch = monotonic()
                try:
                    os.startfile(ACTIVITY_URL)
                except OSError:
                    pass
    if not received.wait(40) and not bridge.closed:
        ctypes.windll.user32.MessageBoxW(
            None, '这批 STEP 已保存。尚未收到浏览器扩展的响应，请检查 Edge 或 Chrome 中已启用新版扩展。\n\n'
            + str(directory), TITLE, 0x30,
        )


def _publish_batch(request):
    global _bridge
    try:
        if _bridge is None:
            _bridge = QuoteBridge()
        received = _bridge.add(request.files, request.auto_confirm)
    except (OSError, ValueError) as error:
        raise QuoteError(f'这批 STEP 已保存，浏览器交接失败。\n{request.directory}\n{error}') from error
    Thread(target=_watch_browser, args=(_bridge, received, request.directory), daemon=True).start()


def _ensure_export_event():
    global _export_event
    if _export_event:
        return
    event = _app.registerCustomEvent(EXPORT_EVENT_ID)
    if not event:
        raise QuoteError('Fusion 无法注册隔离实体导出的事件。请重启 Fusion 后再试。')
    exporter = _RunExport()
    if not event.add(exporter):
        _app.unregisterCustomEvent(EXPORT_EVENT_ID)
        raise QuoteError('Fusion 无法连接隔离实体导出的事件。请重启 Fusion 后再试。')
    _handlers.append(exporter)
    _export_event = event


def _queue_isolated_export(app, request):
    # Only the event signal crosses threads. All modeling calls stay on Fusion's UI thread.
    for _ in range(30):
        if request.done.wait(0.4):
            return
        try:
            app.fireCustomEvent(EXPORT_EVENT_ID, request.token)
        except RuntimeError:
            continue
    if not request.done.is_set():
        request.done.set()
        ctypes.windll.user32.MessageBoxW(
            None, 'Fusion 未能开始隔离实体导出。请结束其他活动命令后重新报价。\n\n' + str(request.directory),
            TITLE, 0x10,
        )


class _RunExport(adsk.core.CustomEventHandler):
    def notify(self, args):
        global _pending_export
        request = _pending_export
        if (request is None or request.done.is_set() or request.token != args.additionalInfo
                or _ui.activeCommand not in ('', 'SelectCommand')):
            return
        request.done.set()
        _pending_export = None
        try:
            for solid, output in request.remaining:
                if not solid.isValid:
                    raise QuoteError(f'实体已不可用。请重新选择零件。\n{output.name}')
                try:
                    _export_solid(solid, output)
                except Exception:
                    output.unlink(missing_ok=True)
                    raise
            _publish_batch(request)
        except Exception as error:
            _ui.messageBox(f'{_describe_error(error)}\n\n导出目录：{request.directory}', TITLE)


def _start_batch(entities, auto_confirm, folder):
    global _pending_export
    if not 1 <= len(entities) <= MAX_FILES:
        raise QuoteError(f'请选择 1 至 {MAX_FILES} 个零件、实体或实体面。')
    if _pending_export is not None and not _pending_export.done.is_set():
        raise QuoteError('上一批零件正在导出，请稍后再试。')

    parts = []
    names = set()
    for entity in entities:
        solid, name = _resolve_part(entity)
        if not solid.isValid:
            raise QuoteError(f'零件已不可用，请重新选择。\n{name}')
        if any(solid == part[0] for part in parts):
            continue
        stem = _file_stem(name)
        unique = stem
        suffix = 2
        while unique.casefold() in names:
            unique = f'{stem} ({suffix})'
            suffix += 1
        names.add(unique.casefold())
        parts.append((solid, unique + '.step'))

    request = _ExportRequest(_batch_directory(folder), auto_confirm)
    for solid, filename in parts:
        output = request.directory / filename
        request.files.append(output)
        component = _direct_component(solid)
        if component is not None:
            try:
                _write_step(component.parentDesign, output, component)
            except (QuoteError, RuntimeError) as error:
                output.unlink(missing_ok=True)
                _app.log(f'FusionQuanzhouQuote: direct STEP export failed for {filename}; trying isolated export. {error}')
            else:
                continue
        request.remaining.append((solid, output))

    if not request.remaining:
        _publish_batch(request)
        return
    # Only exports that need a temporary design wait for the command to finish.
    _ensure_export_event()
    _pending_export = request
    try:
        Thread(target=_queue_isolated_export, args=(_app, request), daemon=True).start()
    except Exception:
        _pending_export = None
        raise


class _Execute(adsk.core.CommandEventHandler):
    def __init__(self, get_options):
        super().__init__()
        self.get_options = get_options

    def notify(self, args):
        try:
            _start_batch(*self.get_options())
        except Exception as error:
            args.executeFailed = True
            args.executeFailedMessage = _describe_error(error)


class _Prefill(adsk.core.CommandEventHandler):
    def __init__(self, selection, entities):
        super().__init__()
        self.selection = selection
        self.entities = entities

    def notify(self, args):
        entities, self.entities = self.entities, []
        if self.selection.selectionCount == 0:
            for entity in entities:
                if entity.isValid and any(_selection_types(entity)):
                    self.selection.addSelection(entity)


class _FolderActions(adsk.core.InputChangedEventHandler):
    def __init__(self, actions, folder):
        super().__init__()
        self.actions = actions
        self.folder = folder

    def notify(self, args):
        if args.input.id != FOLDER_ACTIONS_ID:
            return
        try:
            item = self.actions.selectedItem
            if not item or item.name not in (BROWSE_FOLDER, RESET_FOLDER):
                return
            choose_folder = item.name == BROWSE_FOLDER
            self.actions.listItems.item(0).isSelected = True
            if choose_folder:
                dialog = _ui.createFolderDialog()
                dialog.title = '选择 STEP 保存文件夹'
                current = Path(self.folder.value)
                if current.is_dir():
                    dialog.initialDirectory = str(current)
                if dialog.showDialog() != adsk.core.DialogResults.DialogOK:
                    return
                folder = Path(dialog.folder)
                if not folder.is_absolute() or not folder.is_dir():
                    raise QuoteError('请选择一个可用的文件夹。')
            else:
                folder = _default_export_folder()
            _save_export_folder(folder)
            self.folder.value = str(folder)
            self.folder.tooltip = self.folder.value
        except Exception as error:
            _ui.messageBox(_describe_error(error), TITLE)


class _Release(adsk.core.CommandEventHandler):
    def __init__(self, handlers):
        super().__init__()
        self.handlers = handlers

    def notify(self, args):
        for handler in self.handlers:
            if handler in _handlers:
                _handlers.remove(handler)
        self.handlers.clear()


def _picked_entities():
    selected = _ui.activeSelections
    if selected.count:
        return [selected.item(index).entity for index in range(selected.count)]
    entity = _standalone_body()
    return [entity] if entity else []


class _CreateCommand(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            entities = _picked_entities()
            command = args.command
            command.okButtonText = '打开活动报价'
            command.isRepeatable = False
            inputs = command.commandInputs
            selection = inputs.addSelectionInput(SELECTION_ID, '零件', '可多选零件组件、实体或实体面')
            for selection_filter in FILTERS:
                selection.addSelectionFilter(selection_filter)
            selection.setSelectionLimits(1, MAX_FILES)
            confirmation = inputs.addBoolValueInput(AUTO_ID, '自动确认 6061 报价', True, '', True)
            confirmation.tooltip = '使用官网默认的铝合金 6061。取消勾选可在官网先选材质。'
            folder = inputs.addStringValueInput(FOLDER_ID, 'STEP 保存位置', str(_load_export_folder()))
            folder.isReadOnly = True
            folder.tooltip = folder.value
            actions = inputs.addDropDownCommandInput(
                FOLDER_ACTIONS_ID, '更改位置', adsk.core.DropDownStyles.TextListDropDownStyle
            )
            actions.listItems.add('目录选项', True)
            actions.listItems.add(BROWSE_FOLDER, False)
            actions.listItems.add(RESET_FOLDER, False)
            note = inputs.addTextBoxCommandInput(
                'quanzhou_help', '', '可一次报价多个零件。独立零件会自动选中唯一实体。', 2, True
            )
            note.isFullWidth = True
            folder_actions = _FolderActions(actions, folder)
            command.inputChanged.add(folder_actions)
            handlers = [folder_actions]
            if entities and len(entities) <= MAX_FILES:
                prefill = _Prefill(selection, entities)
                command.activate.add(prefill)
                handlers.append(prefill)

            def get_options():
                selected = [selection.selection(index).entity for index in range(selection.selectionCount)]
                return selected, confirmation.value, Path(folder.value)

            execute = _Execute(get_options)
            release = _Release(handlers)
            command.execute.add(execute)
            command.destroy.add(release)
            handlers.extend((execute, release))
            _handlers.extend(handlers)
        except Exception as error:
            _ui.messageBox(_describe_error(error), TITLE)


class _QuickCommand(adsk.core.CommandCreatedEventHandler):
    def __init__(self, from_menu=False):
        super().__init__()
        self.from_menu = from_menu

    def notify(self, args):
        global _toolbar_selection, _menu_selection
        try:
            command = args.command
            command.isRepeatable = False
            if self.from_menu:
                selected, _menu_selection = _menu_selection, ()
            else:
                selected, _toolbar_selection = _toolbar_selection, None
                if selected is None:
                    selected = _picked_entities()
            execute = _Execute(lambda: (selected, True, _load_export_folder()))
            handlers = [execute]
            release = _Release(handlers)
            handlers.append(release)
            command.execute.add(execute)
            command.destroy.add(release)
            _handlers.extend(handlers)
        except Exception as error:
            _ui.messageBox(_describe_error(error), TITLE)


class _CaptureQuickSelection(adsk.core.ApplicationCommandEventHandler):
    def notify(self, args):
        global _toolbar_selection
        if args.commandId == QUICK_ID:
            _toolbar_selection = _picked_entities()


class _MarkingMenu(adsk.core.MarkingMenuEventHandler):
    def notify(self, args):
        global _menu_selection
        try:
            _menu_selection = ()
            entities = args.selectedEntities
            if 1 <= len(entities) <= MAX_FILES and all(any(_selection_types(entity)) for entity in entities):
                _menu_selection = tuple(entities)
                args.linearMarkingMenu.controls.addCommand(_quick_menu_definition)
                args.linearMarkingMenu.controls.addCommand(_definition)
        except Exception:
            _app.log(traceback.format_exc())


def run(context):
    global _app, _ui, _definition, _control, _quick_definition, _quick_menu_definition
    global _quick_control, _quick_start_handler, _menu_handler, _bridge
    _app = adsk.core.Application.get()
    _ui = _app.userInterface
    if _definition is not None:
        return
    try:
        _definition = _ui.commandDefinitions.addButtonDefinition(
            COMMAND_ID, TITLE, '设置保存位置、选择零件或修改铨洲 6.8 活动报价方式。'
        )
        creator = _CreateCommand()
        _definition.commandCreated.add(creator)
        _handlers.append(creator)
        title = '铨洲一键报价'
        tooltip = '导出所选零件，使用 6061 自动确认铨洲 6.8 活动报价。'
        _quick_definition = _ui.commandDefinitions.addButtonDefinition(QUICK_ID, title, tooltip)
        _quick_menu_definition = _ui.commandDefinitions.addButtonDefinition(QUICK_MENU_ID, title, tooltip)
        for definition, from_menu in ((_quick_definition, False), (_quick_menu_definition, True)):
            quick = _QuickCommand(from_menu)
            definition.commandCreated.add(quick)
            _handlers.append(quick)
        _quick_start_handler = _CaptureQuickSelection()
        _ui.commandStarting.add(_quick_start_handler)

        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            _control = panel.controls.addCommand(_definition)
            _control.isPromoted = True
            _quick_control = panel.controls.addCommand(_quick_definition)
            _quick_control.isPromoted = True
        _menu_handler = _MarkingMenu()
        _ui.markingMenuDisplaying.add(_menu_handler)
        try:
            _bridge = QuoteBridge()
        except OSError as error:
            _app.log(f'FusionQuanzhouQuote: cannot listen on loopback yet: {error}')
    except Exception as error:
        message = _describe_error(error)
        stop(context)
        _ui.messageBox(message, TITLE)


def stop(context):
    global _bridge, _export_event, _definition, _control, _quick_definition, _quick_menu_definition
    global _quick_control, _quick_start_handler, _menu_handler, _pending_export, _toolbar_selection, _menu_selection
    _toolbar_selection = None
    _menu_selection = ()
    if _pending_export is not None:
        _pending_export.done.set()
    _pending_export = None
    if _bridge:
        _bridge.close()
        _bridge = None
    if _menu_handler:
        _ui.markingMenuDisplaying.remove(_menu_handler)
        _menu_handler = None
    if _quick_start_handler:
        _ui.commandStarting.remove(_quick_start_handler)
        _quick_start_handler = None
    if _control:
        _control.deleteMe()
        _control = None
    if _quick_control:
        _quick_control.deleteMe()
        _quick_control = None
    if _definition:
        _definition.deleteMe()
        _definition = None
    if _quick_definition:
        _quick_definition.deleteMe()
        _quick_definition = None
    if _quick_menu_definition:
        _quick_menu_definition.deleteMe()
        _quick_menu_definition = None
    if _export_event:
        _app.unregisterCustomEvent(EXPORT_EVENT_ID)
        _export_event = None
    _handlers.clear()
