---
name: anki-creator
description: Create structured Anki flashcard decks from vocabulary lists. Use when user wants to create vocabulary flashcards for language learning, generate CSV files for Anki import, or process word lists with definitions, examples, synonyms, antonyms, and grammatical information. Supports any language and handles both pasted content and text files.
---

# Anki Creator

Create vocabulary flashcard decks ready for Anki import. Generate CSV files with comprehensive word information including definitions, examples, translations, synonyms, antonyms, and grammar details.

## Quick Start

1. Identify input source (pasted content or file path)
2. Parse the vocabulary list
3. Generate CSV file with appropriate columns

## Input Format

Accept vocabulary lists in this format:

```
- word:
  Example sentence(s) in the source language.

- another_word:
  1. First example sentence.
  2. Second example sentence.

- phrase with article:
  Contextual example showing usage.
```

Input may be:
- Pasted directly in chat
- Provided as a .txt file path

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
| Syn | Three synonyms in source language | No |
| Ant | One antonym in source language | No |
| Word_type | Part of speech (noun, verb, adjective, etc.) | Yes |
| Gram | Grammatical info (article, plural, conjugations, etc.) | No |

## Processing Rules

### Front Field
- Convert plural nouns to singular form
- Convert conjugated verbs to infinitive
- For uninflectable words, retain the form given
- Remove any articles unless they're part of an idiom

### Example Field
- Use ALL provided example sentences
- If multiple examples exist, separate with ` | `
- If no examples provided, create natural, contextual examples
- Number examples in Example_eng if multiple exist

### Gram Field by Word Type

**Nouns:**
- Specify article (de/het/der/die/la/el/los/etc. based on language)
- Include plural form

**Verbs:**
- Past tense form(s)
- Past participle form
- Auxiliary verb (hebben/zijn/have/bean/etc.)

**Adverbs:**
- Type: Causal, Contrasting, Temporal, Additive, or Conditional/Alternative

**Conjunctions:**
- Coordinating or Subordinating

**Other types:**
- Brief descriptive label as appropriate

### Synonyms and Antonyms
- Provide 3 synonyms when applicable
- Provide 1 antonym when applicable
- Use "geen antoniem", "no antonym", or language equivalent when none exists

### Language Handling

Detect source language from input. Common indicators:
- Dutch: de/het articles, plural -en
- German: der/die/das articles
- Spanish: el/la/los/las articles
- French: le/la/les articles

Adapt output language and grammatical categories accordingly.

## Output

Generate a UTF-8 encoded CSV file with semicolon delimiters (Anki-compatible). Filename format: `anki-vocab-[language]-[timestamp].csv`

Example output structure:
```csv
Front;Back;Desc;Desc_eng;Meaning;Example;Example_eng;Syn;Ant;Word_type;Gram
hoop;;veel;erg veel;a lot;Er zijn een hoop dingen veranderd;There are a lot of things changed;veel;stuk;weinig;noun;de hoop
```
