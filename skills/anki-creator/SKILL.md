---
name: anki-creator
description: Create structured Anki flashcard decks from vocabulary lists. Use when user wants to create vocabulary flashcards for language learning, generate CSV files for review or Anki import, or process word lists with definitions, examples, synonyms, antonyms, and grammatical information. Supports any language and handles both pasted content and text files.
license: MIT
metadata:
  category: language-learning
  keywords: vocabulary, flashcards, csv, anki, language
---

# Anki Creator

Create vocabulary flashcard decks ready for Anki import. Generate semicolon-delimited CSV files with comprehensive word information including definitions, examples, translations, synonyms, antonyms, and grammar details.

## Quick Start

```bash
# Create from pasted content
Just paste your vocabulary list and ask to create Anki cards.

# Create from file
Create Anki cards from /path/to/vocab-list.txt

# Specify language explicitly
Create Dutch Anki cards from this vocabulary list:
```

## How It Works

1. **Parse** your vocabulary list (from paste or file)
2. **Detect** the source language automatically
3. **Generate** a semicolon-delimited CSV file compatible with Anki
4. **Import** directly into Anki

## Input Format

Accept vocabulary lists in multiple flexible formats:

### Format 1: Standard with examples
```
- word:
  Example sentence(s) in the source language.

- another_word:
  1. First example sentence.
  2. Second example sentence.
```

### Format 2: Without colons
```
- word
  Example sentence.

- another_word
  Example here.
```

### Format 3: With inline translations
```
- word – English translation
  Example sentence – English translation of example.

- Verblijfsvergunning – Residence permit
  Ik heb een geldige verblijfsvergunning. – I have a valid residence permit.
```

### Format 4: Phrases and complete sentences
```
- Kunt u dat alstublieft herhalen?
  (Can you please repeat that?)

- Hoe laat is het?
  (What time is it?)
```

Input may be:
- Pasted directly in chat
- Provided as a .txt file path

### Parsing Rules
- **Optional colons**: Words may end with `:` or not - both are accepted
- **Inline translations**: Extract existing English translations from `– ` or ` - ` patterns instead of regenerating
- **Phrases as entries**: Complete sentences and phrases are valid vocabulary items (not just single words)
- **Example translations**: When examples have appended `– English translation`, split and use the provided translation
- **Empty examples**: Entries without example sentences should have natural contextual examples generated

## CSV Output Columns

| Column | Description | Required |
|--------|-------------|----------|
| Front | Word in base form (singular for nouns, infinitive for verbs) | Yes |
| Back | Empty field for user notes | Yes |
| Desc | Definition in source language | Yes |
| Desc_eng | Translation of definition to English | Yes |
| Meaning | Concise English translation of the word | Yes |
| Example | Example sentence(s) in source language | Yes |
| Example_eng | English translation of example(s) | Yes |
| Syn | Three synonyms in source language (separated by commas) | No |
| Ant | One antonym in source language | No |
| Word_type | Part of speech (noun, verb, adjective, etc.) | Yes |
| Gram | Grammatical info (article, plural, conjugations, etc.) | No |
| Roots_and_decomposition | Word decomposition: prefixes, roots, suffixes with meanings and how they combine | Yes |

## Processing Rules

### Input Parsing
- **Optional colons**: Handle both `- word:` and `- word` formats
- **Inline translations**: When input contains `word – translation`, extract and use the provided translation directly
- **Example translations**: When examples contain `– English text`, extract and use for Example_eng field
- **Phrases**: For complete sentences/phrases as entries, treat the entire phrase as the Front field
- **Mixed formats**: A single input file may contain entries in multiple formats - handle each according to its pattern

### Front Field
- Convert plural nouns to singular form
- Convert conjugated verbs to infinitive
- For uninflectable words, retain the form given
- Remove any articles unless they're part of an idiom
- For phrases/sentences: keep the original form as-is (do not simplify)

### Example Field
- Use ALL provided example sentences
- If multiple examples exist, separate with ` | `
- If no examples provided, create natural, contextual examples
- Number examples in Example_eng if multiple exist (1. ..., 2. ...)

### Gram Field by Word Type

**Nouns:**
- Specify article (de/het/der/die/la/el/los/etc. based on language)
- Include plural form

**Verbs:**
- Past tense form(s)
- Past participle form
- Auxiliary verb (hebben/zijn/have/been/etc.)

**Adverbs:**
- Type: Causal, Contrasting, Temporal, Additive, or Conditional/Alternative

**Conjunctions:**
- Coordinating or Subordinating

**Other types:**
- Brief descriptive label as appropriate

### Roots_and_decomposition Field
- Break the word into its morphological components: prefixes, root words, and suffixes
- Explain the meaning of each component
- Describe how the components combine to form the word's overall meaning
- For compound words, identify each constituent part
- Examples:
  - `naturalisatie` → `natuur (nature) + -alisatie (process of becoming)` → the process of becoming a citizen/naturalized
  - `verblijfsvergunning` → `verblijf (stay/residence) + vergunning (permit)` → permit for staying/residing
  - `onmogelijk` → `on- (un-/not) + mogelijk (possible)` → impossible
  - `uitspraak` → `uit (out) + spraak (speech)` → pronunciation/statement
- For phrases or sentences: state "N/A" since decomposition does not apply

### Synonyms and Antonyms
- Provide up to 3 synonyms when applicable (comma-separated)
- Provide 1 antonym when applicable
- Use "geen antoniem", "no antonym", or language equivalent when none exists
- Use "geen synoniemen", "no synonyms", or language equivalent when none exist

### Language Handling

Detect source language from input. Common indicators:

| Language | Article Pattern | Example |
|----------|----------------|---------|
| Dutch | de/het | de auto, het huis |
| German | der/die/das | der Hund, die Katze |
| Spanish | el/la/los/las | el libro, la mesa |
| French | le/la/les | le chat, la maison |
| Italian | il/la/i/gli/lo | il ragazzo, la donna |
| Portuguese | o/a/os/as | o livro, a casa |
| Swedish | en/ett | en bil, ett hus |

Adapt output language and grammatical categories accordingly.

## Output

Generate a UTF-8 encoded CSV file with semicolon delimiters (Anki-compatible). Filename format: `anki-vocab-[language]-[timestamp].csv`

**CRITICAL: Quoting Rule** — Any field value that contains a semicolon (`;`), double quote (`"`), or newline MUST be wrapped in double quotes. Double quotes inside a field must be escaped as `""`. This prevents Anki from misinterpreting field content as column boundaries. Apply this quoting to every row, not just the header.

Example output structure (with proper quoting):
```csv
Front;Back;Desc;Desc_eng;Meaning;Example;Example_eng;Syn;Ant;Word_type;Gram;Roots_and_decomposition
hoop;;veel;erg veel;a lot;Er zijn een hoop dingen veranderd.;There are a lot of things changed.;veel,menigte,stuk;weinig;noun;de hoop;"hoop (heap/pile) — from Middle Dutch hop, referring to a pile or large quantity"
lopen;;"gaan te voet; wandelen";to go on foot;Ik loop naar school.;I walk to school.;stappen,driften,wandelen;rennen;verb;"liep, gelopen, hebben";lopen (to walk/run) — from Middle Dutch lōpen, of Germanic origin
```

In the second row above, the Desc field contains a semicolon so it is quoted: `"gaan te voet; wandelen"`. Similarly, the Gram field `"liep, gelopen, hebben"` is quoted.

## Importing into Anki

1. Open Anki
2. Go to **File → Import** (or press `Ctrl/Cmd + I`)
3. Select the generated CSV file
4. Configure import settings:
   - **Type**: Select your note type or create a new one matching the columns
   - **Deck**: Choose target deck
   - **Delimiter**: Semicolon (`;`)
5. Click **Import** to add cards to your deck

### Creating a Custom Note Type

If you don't have a matching note type:

1. Go to **Tools → Manage Note Types**
2. Click **Add** → **Add: Basic**
3. Rename to "Vocabulary Card"
4. Go to **Fields** and add all required fields
5. Go to **Cards** to design your card template
6. Use `{{Front}}`, `{{Back}}`, `{{Example}}`, etc. in templates

## Troubleshooting

**Issue: Import fails with encoding errors**
- Ensure the CSV file is UTF-8 encoded
- Check that special characters (accents, umlauts) are preserved

**Issue: Cards don't display correctly**
- Verify the semicolon delimiter is selected in Anki import dialog
- Check that column names match your note type fields exactly

**Issue: Fields are misaligned after import (some records have shifted columns)**
- A field value contains the semicolon delimiter (`;`), causing Anki to split it into two columns
- Fix: ensure all fields containing `;` or `"` are properly double-quoted in the CSV
- This is the most common cause of misaligned fields — always quote fields with special characters

**Issue: Examples are cut off**
- Anki handles long text fields well, but consider splitting very long examples

**Issue: Language detection is wrong**
- Specify the language explicitly in your request
- Add language-specific context (e.g., "Dutch vocabulary list")

## Examples

### Dutch Vocabulary (Mixed Format)
```
- naturalisatie

- naturalisatieaanvraag
  Ik heb een afspraak voor een naturalisatieaanvraag.
  De kosten voor de naturalisatieaanvraag zijn €1790.

- Verblijfsvergunning – Residence permit
  Ik heb een geldige verblijfsvergunning. – I have a valid residence permit.

- Kunt u dat alstublieft herhalen?
- Kunt u iets langzamer spreken, alstublieft?
```

Output handles:
- Words without colons (`naturalisatie`)
- Words with multiple examples (`naturalisatieaanvraag`)
- Inline translations (`Verblijfsvergunning – Residence permit`)
- Complete phrases as vocabulary items (`Kunt u dat alstublieft herhalen?`)

### Standard Dutch Vocabulary
```
- de appel:
  Ik eet elke dag een appel.

- lopen:
  1. Ik loop naar school.
  2. Wij lopen in het park.

- groot:
  Dit is een groot huis.
```

Output includes: Dutch articles, verb conjugations (lopen → liep/gelopen), and appropriate grammatical gender.

### Spanish Vocabulary
```
- el libro:
  Leo un libro interesante.

- hablar:
  1. ¿Puedes hablar más despacio?
  2. Me gusta hablar con amigos.
```

Output includes: Spanish articles, verb infinitives, and grammatical gender.

## Tips

- Group related words together (e.g., all food vocabulary, all verbs)
- Include context-specific examples for better retention
- Review generated CSV before importing to catch any parsing errors
- Use tags in Anki to organize cards by topic or difficulty level
- Consider adding audio fields later for pronunciation practice
