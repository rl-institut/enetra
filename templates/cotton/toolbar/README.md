# Toolbar

Renders the full section toolbar: search input, sort dropdown, card/table view toggle, and a primary create button.

## Props

| Prop | Type | Default | Description |
|---|---|---|---|
| `search_placeholder` | string | `"Durchsuchen..."` | Placeholder text for the search input |
| `create_label` | string | `"Erstellen"` | Label for the primary create button |
| `create_icon` | string | `"plus-circle"` | Lucide icon name rendered inside the create button |

## Slots

| Slot | Description |
|---|---|
| `card_view` | Content shown when the user selects the card view. Responsible for its own wrapper element and layout classes (e.g. `grid grid-cols-3 gap-5`). |
| `table_view` | Content shown when the user selects the table view. Responsible for its own wrapper element (e.g. the rounded container div around the `<table>`). |

## Notes

- Sort options (Name / Erstellt) are fixed. Both pages use the same options, so they are hardcoded.
