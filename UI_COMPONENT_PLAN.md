# Power Automate Connector — UI component plan

Источники: `Docs/session-notes/UI_COMPONENT_VOCABULARY.md`, `UI_INTERFACE_STANDARD.md`,
`concepts/panels.md`. Основано на функционале `power-automate-connector`.

## 1. Компоненты

| Экран | Примитивы | Почему именно эти |
|---|---|---|
| Sidebar (left) | `ui.Column`(align="start") + `ui.Text`(environment name) + `ui.Divider` + navigation `ui.ListItem`(Flows/Runs) + `ui.Button`("App settings") | Без карточек по стандарту. |
| Flow List (center, `center_overlay=True`) | `ui.Stats`(Active/Suspended/Failed runs today) + `ui.DataTable`(name, state Toggle-колонка через editable=True edit_type="toggle", modified; sortable) | Активация/приостановка cloud flow прямо из таблицы через editable toggle-колонку. |
| Flow Detail | Back-button + `ui.KeyValue`(trigger type/created/modified) + `ui.Graph`(nodes=шаги flow, edges=порядок выполнения) + `ui.Button`("Run History") | `Graph` — подходящий примитив для визуализации структуры шагов Power Automate flow. |
| Run List | `ui.DataTable`(started_at, status Badge Succeeded/Failed/Running, duration; sortable) | Табличная история запусков flow. |
| Run Detail | Back-button + `ui.KeyValue`(flow/status/duration) + `ui.Code`(language="json", trigger inputs/outputs, readonly) + `ui.Button`("Resubmit") | `Code`(json) — для просмотра сырых данных запуска. |
| App Settings | `ui.Accordion`([Connections+Disconnect, Environment Select, Azure AD App Registration info]) | Централизованные настройки по стандарту. |

## 2. User flow (валидно по panel lifecycle)

1. **SESSION INIT** → `__panel__pa_sidebar` рендерит environment + разделы,
   `auto_action` открывает Flow List для окружения по умолчанию.
2. Flow List: DataTable с editable toggle "State" → `on_cell_edit` вызывает
   `set_flow_state` напрямую (обратимо) → `refresh_panels`.
3. Клик на строку (не toggle) → `ui.Call(flow_id=...)` → Flow Detail на том же
   center handler — `Graph` рендерит шаги flow.
4. "Run History" → Run List (параметр `flow_id` сохраняется) → клик на строку →
   Run Detail → "Resubmit" — прямой Call.
5. App Settings — единственная точка входа через кнопку в сайдбаре.

## 3. Экраны (конкретно, по файлам `panels.py`)

1. `pa_sidebar` (`slot="left"`) — навигация, App settings button, Environment
   Select если несколько окружений (`ui.Select` прямо в сайдбаре).
2. `pa_center` (`slot="center"`, `center_overlay=True`) — параметризован `view`
   (flows/flow_detail/runs/run_detail).
3. `pa_settings` (`slot="center"`, `panels_settings.py`) — Accordion с
   Connections/Environment/App Registration.
