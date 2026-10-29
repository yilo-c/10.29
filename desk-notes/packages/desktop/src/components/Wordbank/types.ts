export interface Wordbook {
  id: number
  name: string
  description?: string
  created_at?: string
}

export interface Word {
  id: number
  word: string
  meaning: string
  example: string
  examples: any[]
  wordbook_id: number
  next_review?: string
  repetitions?: number
  efactor?: number
  phonetic?: string
  level?: string
  etymology?: string
  synonyms?: { word: string; similarity?: string; nuance?: string }[]
  antonyms?: { word: string; note?: string }[]
  collocations?: { phrase: string; meaning?: string }[]
}

export interface ReviewStats {
  remembered: number
  forgotten: number
}
