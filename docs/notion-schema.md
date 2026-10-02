# Esquema de Notion para argospipe

Contrato entre la base de ofertas en Notion (hoy `Argos — jobs`, la llena el bot) y argospipe.
Dice qué propiedades lee argospipe, cuáles escribe y cuáles no toca.

Los nombres de las propiedades son configurables en `config.yaml`. Los de abajo son los de
`Argos — jobs` y los defaults recomendados.

## 1. Propiedades que argospipe lee

| Propiedad | Tipo en Notion | Campo de `RawJob` | Obligatoria | Notas |
|---|---|---|---|---|
| `Name` | title | `title` | sí | Título del puesto |
| `Company` | text | `company` | sí | |
| `Link` | url | `url` | sí | URL pública de la oferta |
| **`Description`** | **text** | `description` | **sí, para el matching** | **Nueva.** Ver §2 |
| `Geo` | text | `location` | no | Texto libre (ej. `Buenos Aires, Argentina`) |
| `Found` | date | `posted_at` | no | Fecha en que el bot encontró la oferta |
| `Status` | select | — | no | Filas con `Closed` no se leen (configurable) |

Campos que no salen de una propiedad:

- `external_id` = id de la página de Notion. Estable aunque cambie el título.
- `source` = `notion:<database_id>`.
- `source_name` = no hay propiedad hoy; queda vacío.
- Lectura incremental: solo filas con `last_edited_time` posterior a la última corrida (campo de
  sistema, no hace falta crearlo).

Una fila sin `Name`, `Company` o `Link` se saltea y aparece como error de la fuente en el reporte.
Una fila sin `Description` se lee igual, queda marcada `missing_description` y **no** va al LLM.

## 2. `Description` — la descripción completa

- Tipo **text** (rich text) de texto libre. Va en la propiedad, no en el cuerpo de la página.
- Contenido: la descripción **completa** de la oferta, tal como la publica la empresa: rol,
  requisitos, stack, modalidad, ubicación, beneficios. Texto plano; el formato se ignora.
- **Límite de Notion:** cada bloque de rich text admite hasta 2.000 caracteres, y una propiedad
  admite hasta 100 bloques. El bot tiene que **partir** la descripción en bloques de ≤ 2.000
  caracteres. argospipe concatena todos los bloques en orden.
- No resumir ni traducir. `Why` y `Notes` ya son el resumen del bot; el matching necesita el
  texto original como dato.
- Vacía o solo espacios → `missing_description`.

## 3. Propiedades que argospipe escribe (`--notion-writeback`, MDG-175)

argospipe **no crea** propiedades. Si falta alguna, el writeback falla con un error claro antes
de tocar ninguna fila. Hay que crearlas a mano una vez:

| Propiedad | Tipo en Notion | Qué escribe |
|---|---|---|
| `Argos score` | number | Puntaje 0–100 del matching |
| `Argos estado` | select: `recomendada`, `descartada`, `sin descripción`, `fallida` | Resultado de la corrida para esa fila |
| `Argos motivo` | text | Motivo del descarte o del fallo (vacío si es `recomendada`) |
| `Argos resumen` | text | Resumen de una línea del matching |
| `Argos gaps` | text | Lo que le falta al perfil, separado por `; ` |

- `recomendada` = `score >= threshold`. `descartada` cubre el prefiltro, el ranking y un score
  bajo; el motivo dice cuál.
- Correr dos veces con el mismo resultado no cambia nada. argospipe solo escribe estas 5
  propiedades.

## 4. Propiedades que argospipe no toca

| Propiedad | Dueño |
|---|---|
| `Why`, `Notes`, `Match`, `Stack`, `English`, `Type`, `Track`, `Salary` | el bot |
| `CV fit`, `CV nota` | la skill `cv-fit` |
| `Status` | Mariano (argospipe solo lo lee) |

`Type`, `Stack` y `English` pueden servir como pistas para el prefiltro en una versión futura.
En v0.1 argospipe deduce modalidad, stack e idioma de la descripción (`core/extract.py`).

## 5. Mapeo en `config.yaml`

```yaml
sources:
  - type: notion
    database_id: "6e93ce47f5bc47a0bdbdb5f135f0a980"   # Argos — jobs
    fields:
      title: "Name"
      company: "Company"
      url: "Link"
      description: "Description"
      location: "Geo"
      posted_at: "Found"
```

Token de la integración en la variable de entorno `NOTION_TOKEN` (MDG-168). La integración
necesita acceso de lectura y, para el writeback, de escritura sobre la base.
