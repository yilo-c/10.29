import { useState, useEffect, useCallback } from 'react'
import { callRpc } from '../../../lib/rpcClient'
import { tokenManager } from '../../../lib/tokenManager'
import { isAiConfigured, translateText, speakText } from '../../../lib/mobileAiUtils'
import { swallowError } from '../../../lib/swallowError'

interface Wordbook { id: number; name: string; word_count?: number }
interface Word { id: number; word: string; meaning: string; example: string; examples?: string[]; repetitions?: number; efactor?: number; interval?: number; next_review?: string }

export default function WordbookView() {
  const [tab, setTab] = useState<'books' | 'translate'>('books')
  const [books, setBooks] = useState<Wordbook[]>([])
  const [words, setWords] = useState<Word[]>([])
  const [activeBook, setActiveBook] = useState<Wordbook | null>(null)
  const [search, setSearch] = useState('')
  const [addWord, setAddWord] = useState('')
  const [addMeaning, setAddMeaning] = useState('')
  const [addExample, setAddExample] = useState('')
  const [aiTranslating, setAiTranslating] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [editWord, setEditWord] = useState<Word | null>(null)
  const [reviewMode, setReviewMode] = useState(false)
  const [reviewWord, setReviewWord] = useState<Word | null>(null)
  const [showMeaning, setShowMeaning] = useState(false)
  const [transInput, setTransInput] = useState('')
  const [transResult, setTransResult] = useState('')
  const [transLoading, setTransLoading] = useState(false)
  const [detailWord, setDetailWord] = useState<Word | null>(null)
  const [wordDetail, setWordDetail] = useState<any>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [dueCount, setDueCount] = useState(0)

  // Lookup word from example sentences (overlay, doesn't close detail)
  const [lookupWord, setLookupWord] = useState<string | null>(null)
  const [lookupResult, setLookupResult] = useState<any>(null)
  const [lookupLoading, setLookupLoading] = useState(false)

  const isPaired = tokenManager.isPaired

  const loadBooks = useCallback(async () => {
    if (!isPaired) return
    try { setBooks(await callRpc<Wordbook[]>('wordbook.list') || []) } catch { setBooks([]) }
  }, [isPaired])

  const loadWords = useCallback(async (bookId: number) => {
    try { setWords(await callRpc<Word[]>('word.list', { wordbook_id: bookId, keyword: search }) || []) } catch { setWords([]) }
  }, [search])

  useEffect(() => { loadBooks() }, [loadBooks])
  useEffect(() => { if (activeBook) loadWords(activeBook.id) }, [activeBook, loadWords])

  // Load due word count for badge
  useEffect(() => {
    if (!isPaired) return
    const loadDue = async () => {
      try { const r = await callRpc<{ count: number }>('word.count_due'); setDueCount(r.count) } catch (e) { swallowError('WordbookView:55', e) }
    }
    loadDue()
    const timer = setInterval(loadDue, 30000)
    return () => clearInterval(timer)
  }, [isPaired])

  const filtered = search ? words.filter(w => w.word.includes(search) || w.meaning.includes(search)) : words

  // Handle wordbook rename
  const handleRenameWordbook = async (wb: Wordbook) => {
    const name = prompt('重命名词本', wb.name)
    if (name?.trim()) {
      try { await callRpc('wordbook.rename', { wordbook_id: wb.id, name: name.trim() }); loadBooks() } catch (e) { swallowError('WordbookView:68', e) }
    }
  }

  // Handle wordbook delete
  const handleDeleteWordbook = async (wb: Wordbook) => {
    if (confirm(`删除词本「${wb.name}」？词内的单词不会丢失`)) {
      try { await callRpc('wordbook.delete', { wordbook_id: wb.id }); setActiveBook(null); loadBooks() } catch (e) { swallowError('WordbookView:75', e) }
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '16px 20px 0', flexShrink: 0 }}>
        <h1 style={{ fontSize: 34, fontWeight: 700, color: 'var(--text-primary)', margin: 0 }}>词库</h1>
        {!isPaired && <div style={{ fontSize: 12, color: 'var(--text-tertiary)', marginTop: 4 }}>配对桌面端后使用</div>}
        <div style={{ display: 'flex', gap: 16, marginTop: 12 }}>
          {(['books', 'translate'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)} style={{ padding: '6px 0', border: 'none', background: 'none', fontSize: 15, fontWeight: tab===t?600:400, color: tab===t?'var(--accent)':'var(--text-tertiary)', cursor: 'pointer', borderBottom: tab===t?'2px solid var(--accent)':'2px solid transparent', display: 'flex', alignItems: 'center', gap: 6 }}>
              {t === 'books' ? '词本' : '翻译'}
              {t === 'books' && dueCount > 0 && (
                <span style={{ background: 'var(--accent)', color: '#fff', fontSize: 10, fontWeight: 600, padding: '1px 6px', borderRadius: 10, minWidth: 18, textAlign: 'center' }}>
                  {dueCount > 99 ? '99+' : dueCount}
                </span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* ── Review Mode ── */}
      {reviewMode && reviewWord ? (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 20, gap: 24 }}>
          <div style={{ fontSize: 32, fontWeight: 700, color: 'var(--text-primary)' }}>{reviewWord.word}</div>
          {showMeaning && (
            <div style={{ textAlign: 'center', padding: '0 20px', lineHeight: 1.6 }}>
              <div style={{ fontSize: 16, color: 'var(--text-secondary)' }}>{reviewWord.meaning}</div>
              {reviewWord.example ? <div style={{ fontSize: 13, color: 'var(--text-tertiary)', marginTop: 8, fontStyle: 'italic' }}>"{reviewWord.example}"</div> : null}
            </div>
          )}
          <button onClick={() => speakText(reviewWord.word)} style={{ background: 'none', border: 'none', fontSize: 20, cursor: 'pointer' }}>🔊</button>
          {!showMeaning ? (
            <button onClick={() => setShowMeaning(true)} style={{ padding: '12px 40px', borderRadius: 12, border: 'none', background: 'var(--accent)', color: '#fff', fontSize: 16, fontWeight: 600, cursor: 'pointer' }}>显示释义</button>
          ) : (
            <div style={{ display: 'flex', gap: 8 }}>
              {[1,2,3,4,5].map(q => (
                <button key={q} onClick={async () => {
                  try { await callRpc('word.review', { word_id: reviewWord.id, quality: q }) } catch (e) { swallowError('WordbookView:115', e) }
                  const idx = words.indexOf(reviewWord)
                  if (idx+1 < words.length) { setReviewWord(words[idx+1]); setShowMeaning(false) }
                  else { setReviewMode(false); setReviewWord(null) }
                }} style={{ width: 44, height: 44, borderRadius: '50%', border: 'none', background: q<=2?'rgba(239,68,68,0.15)':q===3?'rgba(251,191,36,0.15)':'rgba(34,197,94,0.15)', color: q<=2?'#ef4444':q===3?'#fbbf24':'#22c55e', fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>{q}</button>
              ))}
            </div>
          )}
          <button onClick={() => { setReviewMode(false); setReviewWord(null) }} style={{ background: 'none', border: 'none', color: 'var(--text-tertiary)', fontSize: 13, cursor: 'pointer' }}>结束复习</button>
        </div>
      ) : tab === 'translate' ? (
        /* ── Translate ── */
        <div style={{ flex: 1, overflowY: 'auto', padding: 16 }}>
          <textarea value={transInput} onChange={e => setTransInput(e.target.value)}
            placeholder="输入要翻译的文本..."
            rows={4}
            style={{ width: '100%', padding: 12, borderRadius: 10, border: '0.5px solid var(--border-default)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 14, outline: 'none', resize: 'none', boxSizing: 'border-box' }} />
          <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
            <button onClick={async () => {
              if (!transInput.trim() || !isAiConfigured()) return; setTransLoading(true)
              let result = ''
              try { result = await translateText(transInput.trim()); setTransResult(result) } catch { setTransResult('翻译失败') }
              if (result && isPaired) {
                callRpc('translations.save', { source_text: transInput.trim(), translated_text: result, source_lang: 'auto', target_lang: 'zh', mode: 'quick' }).catch(() => {})
              }
              setTransLoading(false)
            }} disabled={!transInput.trim() || transLoading || !isAiConfigured()}
              style={{ flex: 1, padding: '10px 0', borderRadius: 8, border: 'none', background: isAiConfigured()&&transInput.trim()?'var(--accent)':'var(--bg-tertiary)', color: isAiConfigured()&&transInput.trim()?'#fff':'var(--text-disabled)', fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
              {transLoading ? '翻译中…' : 'AI 翻译'}
            </button>
            <button onClick={() => speakText(transInput.trim())} disabled={!transInput.trim()}
              style={{ width: 44, height: 44, borderRadius: 8, border: '0.5px solid var(--border-default)', background: 'none', fontSize: 18, cursor: 'pointer' }}>🔊</button>
          </div>
          {transResult && (
            <div style={{ marginTop: 12, padding: 12, borderRadius: 10, background: 'var(--bg-secondary)', border: '0.5px solid var(--border-subtle)' }}>
              <div style={{ fontSize: 14, color: 'var(--text-primary)', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{transResult}</div>
              {activeBook && (
                <button onClick={async () => {
                  try { await callRpc('word.add', { wordbook_id: activeBook.id, word: transInput.trim().slice(0,50), meaning: transResult, example: '' }) } catch (e) { swallowError('WordbookView:153', e) }
                  setTransResult(''); setTransInput('')
                }} style={{ marginTop: 8, padding: '6px 12px', borderRadius: 6, border: 'none', background: 'rgba(52,211,153,0.12)', color: '#34d399', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}>
                  + 添加到词本
                </button>
              )}
            </div>
          )}
        </div>
      ) : (
        /* ── Books ── */
        <>
          <div style={{ display: 'flex', gap: 6, overflowX: 'auto', padding: '8px 20px', flexShrink: 0, scrollbarWidth: 'none' }}>
            {books.map(b => (
              <div key={b.id} style={{ position: 'relative' }}>
                <button onClick={() => { setActiveBook(b); setExpandedId(null) }}
                  onContextMenu={e => { e.preventDefault(); const action = confirm('重命名点确定，删除点取消'); if (action) handleRenameWordbook(b); else handleDeleteWordbook(b) }}
                  style={{ padding: '6px 14px', borderRadius: 20, border: '0.5px solid var(--border-subtle)', whiteSpace: 'nowrap', background: activeBook?.id===b.id?'var(--accent-surface)':'transparent', color: activeBook?.id===b.id?'var(--accent)':'var(--text-secondary)', fontSize: 13, fontWeight: activeBook?.id===b.id?600:400, cursor: 'pointer' }}>
                  {b.name}
                </button>
              </div>
            ))}
            <button onClick={async () => {
              const n = prompt('词本名称')
              if (n?.trim()) { try { await callRpc('wordbook.create', { name: n.trim() }); loadBooks() } catch (e) { swallowError('WordbookView:177', e) } }
            }} style={{ padding: '6px 14px', borderRadius: 20, border: '1px dashed var(--border-default)', whiteSpace: 'nowrap', background: 'none', color: 'var(--text-tertiary)', fontSize: 13, cursor: 'pointer' }}>+ 新建</button>
          </div>

          {activeBook && (
            <div style={{ display: 'flex', gap: 8, padding: '0 20px 4px', alignItems: 'center' }}>
              <input value={search} onChange={e => setSearch(e.target.value)} placeholder="搜索单词..." style={{ flex: 1, padding: '6px 10px', borderRadius: 6, border: '0.5px solid var(--border-default)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 12, outline: 'none' }} />
              <button onClick={async () => {
                setReviewMode(true)
                try { const due = await callRpc<Word[]>('word.list_due', { wordbook_id: activeBook.id }); const list = due?.length?due:words; if (list.length) { setReviewWord(list[0]); setShowMeaning(false) } } catch { if (words.length) { setReviewWord(words[0]); setShowMeaning(false) } }
              }} style={{ padding: '6px 12px', borderRadius: 6, border: 'none', background: 'rgba(168,85,247,0.12)', color: '#a855f7', fontSize: 11, cursor: 'pointer', whiteSpace: 'nowrap' }}>复习</button>
            </div>
          )}

          {activeBook && (
            <div style={{ display: 'flex', gap: 6, padding: '4px 20px 8px', alignItems: 'center' }}>
              <input value={addWord} onChange={e => setAddWord(e.target.value)} placeholder="单词" style={{ width: 100, padding: '6px 10px', borderRadius: 6, border: '0.5px solid var(--border-default)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 12, outline: 'none' }} />
              <input value={addMeaning} onChange={e => setAddMeaning(e.target.value)} placeholder="释义" style={{ flex: 1, padding: '6px 10px', borderRadius: 6, border: '0.5px solid var(--border-default)', background: 'var(--bg-secondary)', color: 'var(--text-primary)', fontSize: 12, outline: 'none' }} />
              {isAiConfigured() && (
                <button onClick={async () => {
                  if (!addWord.trim()) return; setAiTranslating(true)
                  try { setAddMeaning(await translateText(addWord.trim())) } catch (e) { swallowError('WordbookView:198', e) }
                  setAiTranslating(false)
                }} disabled={aiTranslating||!addWord.trim()} style={{ padding: '6px 10px', borderRadius: 6, border: 'none', background: 'rgba(168,85,247,0.12)', color: '#a855f7', fontSize: 11, cursor: 'pointer', whiteSpace: 'nowrap', opacity: aiTranslating?.5:1 }}>
                  {aiTranslating ? '...' : 'AI'}
                </button>
              )}
              <button onClick={async () => {
                if (!addWord.trim() || !activeBook) return
                try { await callRpc('word.add', { wordbook_id: activeBook.id, word: addWord.trim(), meaning: addMeaning.trim(), example: addExample.trim() }); setAddWord(''); setAddMeaning(''); setAddExample(''); loadWords(activeBook.id) } catch (e) { swallowError('WordbookView:206', e) }
              }} disabled={!addWord.trim()} style={{ padding: '6px 12px', borderRadius: 6, border: 'none', background: 'var(--accent)', color: '#fff', fontSize: 11, fontWeight: 600, cursor: 'pointer', opacity: addWord.trim()?1:0.5 }}>添加</button>
            </div>
          )}

          <div style={{ flex: 1, overflowY: 'auto', padding: '0 20px 20px' }}>
            {!activeBook && books.length>0 && <div style={{ textAlign:'center', padding:40, color:'var(--text-tertiary)', fontSize:13 }}>选择一个词本</div>}
            {activeBook && filtered.length===0 && <div style={{ textAlign:'center', padding:40, color:'var(--text-tertiary)', fontSize:13 }}>{search?'没有匹配的单词':'暂无单词'}</div>}

            {filtered.map(w => {
              const isExpanded = expandedId === w.id
              // Mastery color: gray=new(0), yellow=learning(1-2), green=mastered(3+)
              const masterColor = !w.repetitions || w.repetitions === 0 ? 'var(--border-subtle)' : w.repetitions <= 2 ? '#fbbf24' : '#22c55e'
              return (
                <div key={w.id} style={{ borderBottom: '0.5px solid var(--border-subtle)', padding: '10px 0', display: 'flex', alignItems: 'stretch' }}>
                  <div style={{ width: 3, borderRadius: 2, background: masterColor, marginRight: 8, flexShrink: 0 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div onClick={async () => { setDetailWord(w); setWordDetail(null); setDetailLoading(false); if (isPaired) try { setWordDetail(await callRpc("translate.word_detail", { word: w.word })) } catch (e) { swallowError('WordbookView:223', e) } }} style={{ display:'flex', alignItems:'flex-start', gap:10, cursor:'pointer' }}>
                      <div style={{ flex:1 }}>
                        <div style={{ fontSize:15, fontWeight:600, color:'var(--text-primary)' }}>{w.word}</div>
                        <div style={{ fontSize:13, color:'var(--text-secondary)', marginTop:2 }}>{w.meaning}</div>
                        {w.example ? <div style={{ fontSize:12, color:'var(--text-tertiary)', marginTop:2, fontStyle:'italic' }}>"{w.example}"</div> : null}
                      </div>
                      <button onClick={e => { e.stopPropagation(); speakText(w.word) }} style={{ background:'none', border:'none', fontSize:16, cursor:'pointer', padding:4 }}>🔊</button>
                    </div>

                    {isExpanded && (
                      <div style={{ marginTop:8, padding:'10px 12px', borderRadius:8, background:'var(--bg-secondary)' }}>
                        <div style={{ display:'flex', gap:16, fontSize:11, color:'var(--text-tertiary)', marginBottom:10, flexWrap:'wrap' }}>
                          {w.repetitions!==undefined && <span>复习 {w.repetitions} 次</span>}
                          {w.efactor!==undefined && <span>EF: {w.efactor.toFixed(1)}</span>}
                          {w.interval!==undefined && <span>间隔 {w.interval} 天</span>}
                          {w.next_review && <span>下次: {new Date(w.next_review).toLocaleDateString('zh-CN')}</span>}
                        </div>
                        {editWord?.id === w.id ? (
                          <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
                            <input value={editWord.meaning} onChange={e => setEditWord({...editWord, meaning:e.target.value})} placeholder="释义" style={{ padding:'6px 10px', borderRadius:6, border:'0.5px solid var(--border-default)', background:'var(--bg-primary)', color:'var(--text-primary)', fontSize:12, outline:'none' }} />
                            <input value={editWord.example||''} onChange={e => setEditWord({...editWord, example:e.target.value})} placeholder="例句" style={{ padding:'6px 10px', borderRadius:6, border:'0.5px solid var(--border-default)', background:'var(--bg-primary)', color:'var(--text-primary)', fontSize:12, outline:'none' }} />
                            <div style={{ display:'flex', gap:6 }}>
                              <button onClick={async () => {
                                try { await callRpc('word.update', { word_id: editWord.id, meaning: editWord.meaning, example: editWord.example||'' }); setEditWord(null); loadWords(activeBook!.id) } catch (e) { swallowError('WordbookView:246', e) }
                              }} style={{ padding:'6px 14px', borderRadius:6, border:'none', background:'var(--accent)', color:'#fff', fontSize:11, cursor:'pointer' }}>保存</button>
                              <button onClick={() => setEditWord(null)} style={{ padding:'6px 14px', borderRadius:6, border:'none', background:'var(--bg-tertiary)', color:'var(--text-secondary)', fontSize:11, cursor:'pointer' }}>取消</button>
                            </div>
                          </div>
                        ) : (
                          <div style={{ display:'flex', gap:8 }}>
                            <button onClick={() => setEditWord({...w})} style={{ padding:'4px 12px', borderRadius:4, border:'0.5px solid var(--border-default)', background:'transparent', color:'var(--text-secondary)', fontSize:11, cursor:'pointer' }}>编辑</button>
                            <button onClick={async () => { try { await callRpc('word.delete', { word_id: w.id }); loadWords(activeBook!.id) } catch (e) { swallowError('WordbookView:254', e) } }}
                              style={{ padding:'4px 12px', borderRadius:4, border:'0.5px solid var(--border-default)', background:'transparent', color:'var(--danger)', fontSize:11, cursor:'pointer' }}>删除</button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </>
      )}

      {/* ── Word Detail Sheet ── */}
      {detailWord && !lookupWord && (
        <div style={{ position: "fixed", inset: 0, zIndex: 3000, background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "flex-end", justifyContent: "center" }} onClick={() => { setDetailWord(null); setWordDetail(null) }}>
          <div style={{ width: "100%", maxWidth: 400, maxHeight: "85vh", background: "var(--bg-secondary)", borderTopLeftRadius: 16, borderTopRightRadius: 16, overflow: "hidden", display: "flex", flexDirection: "column" }} onClick={e => e.stopPropagation()}>
            {detailLoading ? (
              <div style={{ padding: 40, textAlign: "center", color: "var(--text-tertiary)" }}>加载中...</div>
            ) : (
              <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
                {/* Word + pronunciation */}
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
                  <span style={{ fontSize: 28, fontWeight: 700, color: "var(--text-primary)" }}>{detailWord!.word}</span>
                  <button onClick={() => speakText(detailWord!.word)} style={{ background: "none", border: "none", fontSize: 22, cursor: "pointer" }}>🔊</button>
                </div>

                {/* Local meaning (always available) */}
                {detailWord!.meaning && (
                  <div style={{ fontSize: 15, color: "var(--text-secondary)", marginBottom: 8 }}>{detailWord!.meaning}</div>
                )}

                {/* Phonetic (from RPC) */}
                {wordDetail?.phonetic ? (
                  <div style={{ fontSize: 13, color: "var(--text-tertiary)", marginBottom: 12 }}>
                    {wordDetail.phonetic} {wordDetail.phonetic_us ? "/US " + wordDetail.phonetic_us : ""} {wordDetail.phonetic_uk ? "/UK " + wordDetail.phonetic_uk : ""}
                  </div>
                ) : null}
                {wordDetail?.level ? (
                  <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 4, background: "var(--accent-surface)", color: "var(--accent)", marginBottom: 12, display: "inline-block" }}>{wordDetail.level}</span>
                ) : null}

                {/* Meanings (from RPC) */}
                {wordDetail?.meanings?.length > 0 && (
                  <div style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>释义</div>
                    {wordDetail.meanings.map((m: string, i: number) => (
                      <div key={i} style={{ fontSize: 13, color: "var(--text-primary)", padding: "4px 0", borderBottom: i<wordDetail.meanings.length-1 ? "0.5px solid var(--border-subtle)" : "none" }}>{m}</div>
                    ))}
                  </div>
                )}

                {/* Examples — merge local + RPC, words clickable via lookupWord */}
                {(() => {
                  const allExamples: string[] = []
                  if (detailWord!.example) allExamples.push(detailWord!.example)
                  if (detailWord!.examples?.length) allExamples.push(...detailWord!.examples)
                  if (wordDetail?.examples?.length) {
                    for (const e of wordDetail.examples) {
                      if (!allExamples.includes(e)) allExamples.push(e)
                    }
                  }
                  if (allExamples.length === 0) return null
                  return (
                    <div style={{ marginBottom: 14 }}>
                      <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>例句</div>
                      {allExamples.map((text, i) => (
                        <div key={i} style={{ fontSize: 12, color: "var(--text-tertiary)", fontStyle: "italic", padding: "3px 0", lineHeight: 1.6 }}>
                          "{text.split(/(\b[a-zA-Z]+(?:['-][a-zA-Z]+)?\b)/).map((part, j) => {
                            if (/^[a-zA-Z]+(?:['-][a-zA-Z]+)?$/.test(part) && part.length > 1) {
                              return <span key={j} onClick={() => { setLookupWord(part.toLowerCase()); setLookupResult(null); setLookupLoading(true); if (isPaired) callRpc("translate.word_detail", { word: part.toLowerCase() }).then(r => { setLookupResult(r); setLookupLoading(false) }).catch(() => setLookupLoading(false)) }} style={{ cursor: "pointer", borderBottom: "1px dashed var(--accent)", color: "var(--accent)" }}>{part}</span>
                            }
                            return part
                          })}"
                        </div>
                      ))}
                    </div>
                  )
                })()}

                {/* Synonyms — show "暂未收录" if RPC returned no data */}
                {wordDetail?.synonyms?.length > 0 ? (
                  <div style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>同义词</div>
                    {wordDetail.synonyms.map((s: any, i: number) => (
                      <div key={i} style={{ fontSize: 12, padding: "3px 0", color: "var(--text-primary)" }}><b>{s.word || s}</b>{s.nuance ? <span style={{ color: "var(--text-tertiary)", marginLeft: 8 }}>{s.nuance}</span> : null}</div>
                    ))}
                  </div>
                ) : (
                  <div style={{ marginBottom: 14, fontSize: 11, color: "var(--text-tertiary)" }}>暂未收录同义词</div>
                )}

                {/* Antonyms — show "暂未收录" if RPC returned no data */}
                {wordDetail?.antonyms?.length > 0 ? (
                  <div style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>反义词</div>
                    {wordDetail.antonyms.map((a: any, i: number) => (
                      <div key={i} style={{ fontSize: 12, padding: "3px 0", color: "var(--text-primary)" }}><b>{a.word || a}</b>{a.note ? <span style={{ color: "var(--text-tertiary)", marginLeft: 8 }}>{a.note}</span> : null}</div>
                    ))}
                  </div>
                ) : (
                  <div style={{ marginBottom: 14, fontSize: 11, color: "var(--text-tertiary)" }}>暂未收录反义词</div>
                )}

                {/* Collocations */}
                {wordDetail?.collocations?.length > 0 && (
                  <div style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>常见搭配</div>
                    {wordDetail.collocations.map((c: any, i: number) => (
                      <div key={i} style={{ fontSize: 12, padding: "3px 0", color: "var(--text-primary)" }}><b>{c.phrase || c}</b>{c.meaning ? <span style={{ color: "var(--text-tertiary)", marginLeft: 8 }}>{c.meaning}</span> : null}</div>
                    ))}
                  </div>
                )}

                {/* SM-2 Stats */}
                <div style={{ display: "flex", gap: 12, fontSize: 10, color: "var(--text-tertiary)", marginTop: 8, flexWrap: "wrap" }}>
                  {detailWord!.repetitions !== undefined && <span>复习 {detailWord!.repetitions} 次</span>}
                  {detailWord!.efactor !== undefined && <span>EF: {detailWord!.efactor!.toFixed(1)}</span>}
                  {detailWord!.next_review && <span>下次: {new Date(detailWord!.next_review!).toLocaleDateString("zh-CN")}</span>}
                </div>
              </div>
            )}

            {/* Bottom bar */}
            <div style={{ flexShrink: 0, borderTop: "0.5px solid var(--border-subtle)", padding: "10px 16px 30px", display: "flex", gap: 8 }}>
              <button onClick={() => { setEditWord(detailWord); setDetailWord(null); setWordDetail(null) }}
                style={{ flex: 1, padding: "10px 0", borderRadius: 8, border: "none", background: "var(--accent)", color: "#fff", fontSize: 13, fontWeight: 600, cursor: "pointer" }}>编辑</button>
              <button onClick={() => { setDetailWord(null); setWordDetail(null) }}
                style={{ flex: 1, padding: "10px 0", borderRadius: 8, border: "none", background: "var(--bg-tertiary)", color: "var(--text-secondary)", fontSize: 13, cursor: "pointer" }}>关闭</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Lookup overlay (inside detail, triggered by clicking example word) ── */}
      {detailWord && lookupWord && (
        <div style={{ position: "fixed", inset: 0, zIndex: 4000, background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "flex-end", justifyContent: "center" }} onClick={() => { setLookupWord(null); setLookupResult(null) }}>
          <div style={{ width: "100%", maxWidth: 400, maxHeight: "50vh", background: "var(--bg-secondary)", borderTopLeftRadius: 16, borderTopRightRadius: 16, overflow: "hidden", display: "flex", flexDirection: "column" }} onClick={e => e.stopPropagation()}>
            {lookupLoading ? (
              <div style={{ padding: 30, textAlign: "center", color: "var(--text-tertiary)", fontSize: 13 }}>查询中...</div>
            ) : (
              <div style={{ flex: 1, overflowY: "auto", padding: 20 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 8 }}>
                  <span style={{ fontSize: 22, fontWeight: 700, color: "var(--text-primary)" }}>{lookupWord}</span>
                  <button onClick={() => speakText(lookupWord!)} style={{ background: "none", border: "none", fontSize: 18, cursor: "pointer" }}>🔊</button>
                </div>
                {lookupResult?.phonetic ? (
                  <div style={{ fontSize: 13, color: "var(--text-tertiary)", marginBottom: 8 }}>{lookupResult.phonetic}</div>
                ) : null}
                {lookupResult?.meanings?.length > 0 ? (
                  <div style={{ marginBottom: 10 }}>
                    {lookupResult.meanings.map((m: string, i: number) => (
                      <div key={i} style={{ fontSize: 13, color: "var(--text-primary)", padding: "3px 0" }}>{m}</div>
                    ))}
                  </div>
                ) : (
                  <div style={{ fontSize: 13, color: "var(--text-tertiary)", marginBottom: 10 }}>暂无释义</div>
                )}
                {/* Quick add to wordbook */}
                {activeBook && (
                  <button onClick={async () => {
                    try { await callRpc('word.add', { wordbook_id: activeBook.id, word: lookupWord, meaning: lookupResult?.meanings?.[0] || '', example: '' }); setLookupWord(null); setLookupResult(null) } catch (e) { swallowError('WordbookView:416', e) }
                  }} style={{ padding: "6px 14px", borderRadius: 6, border: "none", background: "rgba(52,211,153,0.12)", color: "#34d399", fontSize: 11, fontWeight: 600, cursor: "pointer", marginRight: 8 }}>
                    + 加入词本
                  </button>
                )}
                <button onClick={() => { setLookupWord(null); setLookupResult(null) }} style={{ padding: "6px 14px", borderRadius: 6, border: "0.5px solid var(--border-default)", background: "transparent", color: "var(--text-secondary)", fontSize: 11, cursor: "pointer" }}>关闭</button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
