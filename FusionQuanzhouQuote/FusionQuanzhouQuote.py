import ctypes
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
from threading import Event, Thread
import traceback
from uuid import uuid4

import adsk.core
import adsk.fusion

from .quote_bridge import ACTIVITY_URL, QuoteBridge


COMMAND_ID = 'Initial_FusionQuanzhouQuote'
EXPORT_EVENT_ID = COMMAND_ID + '_Export'
PANEL_ID = 'SolidScriptsAddinsPanel'
TITLE = '铨洲 6.8 活动报价'
SELECTION_ID = 'quanzhou_part'
AUTO_ID = 'quanzhou_confirm_6061'
FILTERS = ('Occurrences', 'RootComponents', 'SolidBodies', 'SolidFaces')

_app = None
_ui = None
_bridge = None
_export_event = None
_definition = None
_control = None
_menu_handler = None
_handlers = []
_pending_export = None


class QuoteError(Exception):
    pass


@dataclass
class _ExportRequest:
    solid: object
    name: str
    auto_confirm: bool
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
        if component.occurrences.count:
            raise QuoteError('这里选中了装配组件。请展开装配并选中一个零件或实体。')
        solids = [candidate for candidate in component.bRepBodies if candidate.isSolid]
        if len(solids) != 1:
            raise QuoteError('所选组件需要恰好有一个实体。多实体零件请在实体文件夹中点选一个实体。')
        return solids[0], component.name
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


def _export_path(name):
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')[:90].rstrip(' .')
    if not stem:
        stem = 'Part'
    if re.fullmatch(r'CON|PRN|AUX|NUL|COM[0-9¹²³]|LPT[0-9¹²³]', stem.split('.')[0], re.I):
        stem = '_' + stem
    app_data = Path(os.environ.get('LOCALAPPDATA') or Path.home())
    exports = app_data / 'FusionQuanzhouQuote' / 'exports'
    exports.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now().strftime('%Y%m%d-%H%M%S-')
    return Path(mkdtemp(prefix=prefix, dir=exports)) / (stem + '.step')


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


def _open_quote(output, auto_confirm):
    global _bridge
    try:
        if _bridge is None:
            _bridge = QuoteBridge()
        os.startfile(_bridge.add(output, auto_confirm))
    except OSError as error:
        _app.log(str(error))
        try:
            os.startfile(ACTIVITY_URL)
        except OSError:
            _app.log('FusionQuanzhouQuote: could not open default browser.')
        _ui.messageBox(f'STEP 已导出，请在活动页手动选择此文件。\n\n{output}\n\n{ACTIVITY_URL}', TITLE)


def _export_file(request, component=None):
    if not request.solid.isValid:
        raise QuoteError('原实体已不可用，请重新选择。')
    output = _export_path(request.name)
    try:
        if component is None:
            _export_solid(request.solid, output)
        else:
            _write_step(component.parentDesign, output, component)
    except Exception:
        try:
            output.unlink(missing_ok=True)
            output.parent.rmdir()
        except OSError:
            pass
        raise
    return output


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
            None, 'Fusion 未能开始单独实体导出。关闭其他活动命令后，请重新选择实体并打开活动报价。',
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
            _open_quote(_export_file(request), request.auto_confirm)
        except Exception as error:
            _ui.messageBox(_describe_error(error), TITLE)


class _Execute(adsk.core.CommandEventHandler):
    def __init__(self, selection, confirmation):
        super().__init__()
        self.selection = selection
        self.confirmation = confirmation

    def notify(self, args):
        global _pending_export
        try:
            if self.selection.selectionCount != 1:
                raise QuoteError('请选择一个零件。')
            if _pending_export is not None and not _pending_export.done.is_set():
                raise QuoteError('上一个零件正在导出，请稍后再试。')
            solid, name = _resolve_part(self.selection.selection(0).entity)
            request = _ExportRequest(solid, name, self.confirmation.value)
            component = _direct_component(solid)
            if component is not None:
                try:
                    output = _export_file(request, component)
                except (QuoteError, RuntimeError) as error:
                    _app.log(f'FusionQuanzhouQuote: direct STEP export failed, trying an isolated body. {error}')
                else:
                    _open_quote(output, request.auto_confirm)
                    return
            # Only exports that need a temporary design wait for the command to finish.
            _ensure_export_event()
            _pending_export = request
            try:
                Thread(target=_queue_isolated_export, args=(_app, request), daemon=True).start()
            except Exception:
                _pending_export = None
                raise
        except Exception as error:
            args.executeFailed = True
            args.executeFailedMessage = _describe_error(error)


class _Prefill(adsk.core.CommandEventHandler):
    def __init__(self, selection, entity):
        super().__init__()
        self.selection = selection
        self.entity = entity

    def notify(self, args):
        entity, self.entity = self.entity, None
        if entity and entity.isValid and self.selection.selectionCount == 0:
            self.selection.addSelection(entity)


class _Release(adsk.core.CommandEventHandler):
    def __init__(self, handlers):
        super().__init__()
        self.handlers = handlers

    def notify(self, args):
        for handler in self.handlers:
            if handler in _handlers:
                _handlers.remove(handler)
        self.handlers.clear()


class _CreateCommand(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            selected = _ui.activeSelections
            entity = selected.item(0).entity if selected.count == 1 else None
            command = args.command
            command.okButtonText = '打开活动报价'
            command.isRepeatable = False
            inputs = command.commandInputs
            selection = inputs.addSelectionInput(SELECTION_ID, '零件', '请选择一件组件、实体或实体面')
            for selection_filter in FILTERS:
                selection.addSelectionFilter(selection_filter)
            selection.setSelectionLimits(1, 1)
            confirmation = inputs.addBoolValueInput(AUTO_ID, '自动确认 6061 报价', True, '', True)
            confirmation.tooltip = '使用官网默认的铝合金 6061。取消勾选可在官网先选材质。'
            note = inputs.addTextBoxCommandInput(
                'quanzhou_help', '', '导出所选实体，并在铨洲 6.8 起打样页上传和查看报价。', 2, True
            )
            note.isFullWidth = True
            handlers = []
            if entity and any(_selection_types(entity)):
                prefill = _Prefill(selection, entity)
                command.activate.add(prefill)
                handlers.append(prefill)
            execute = _Execute(selection, confirmation)
            release = _Release(handlers)
            command.execute.add(execute)
            command.destroy.add(release)
            handlers.extend((execute, release))
            _handlers.extend(handlers)
        except Exception as error:
            _ui.messageBox(_describe_error(error), TITLE)


class _MarkingMenu(adsk.core.MarkingMenuEventHandler):
    def notify(self, args):
        try:
            entities = args.selectedEntities
            if len(entities) == 1 and any(_selection_types(entities[0])):
                args.linearMarkingMenu.controls.addCommand(_definition)
        except Exception:
            _app.log(traceback.format_exc())


def run(context):
    global _app, _ui, _definition, _control, _menu_handler
    _app = adsk.core.Application.get()
    _ui = _app.userInterface
    if _definition is not None:
        return
    try:
        _definition = _ui.commandDefinitions.addButtonDefinition(
            COMMAND_ID, TITLE, '将单个实体导入铨洲智造 6.8 起打样。'
        )
        creator = _CreateCommand()
        _definition.commandCreated.add(creator)
        _handlers.append(creator)

        panel = _ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            _control = panel.controls.addCommand(_definition)
            _control.isPromoted = True
        _menu_handler = _MarkingMenu()
        _ui.markingMenuDisplaying.add(_menu_handler)
    except Exception as error:
        message = _describe_error(error)
        stop(context)
        _ui.messageBox(message, TITLE)


def stop(context):
    global _bridge, _export_event, _definition, _control, _menu_handler, _pending_export
    if _pending_export is not None:
        _pending_export.done.set()
    _pending_export = None
    if _bridge:
        _bridge.close()
        _bridge = None
    if _menu_handler:
        _ui.markingMenuDisplaying.remove(_menu_handler)
        _menu_handler = None
    if _control:
        _control.deleteMe()
        _control = None
    if _definition:
        _definition.deleteMe()
        _definition = None
    if _export_event:
        _app.unregisterCustomEvent(EXPORT_EVENT_ID)
        _export_event = None
    _handlers.clear()
