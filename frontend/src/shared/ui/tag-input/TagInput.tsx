import { useState, type KeyboardEvent } from 'react'
import styles from './TagInput.module.css'
import { Icon, X } from '../icon'

interface TagInputProps {
  id?: string
  values: string[]
  onChange: (values: string[]) => void
  placeholder?: string
}

export function TagInput({ id, values, onChange, placeholder }: TagInputProps) {
  const [draft, setDraft] = useState('')

  const commit = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      const next = draft.trim()
      if (next && !values.includes(next)) onChange([...values, next])
      setDraft('')
    }
    if (event.key === 'Backspace' && !draft && values.length) onChange(values.slice(0, -1))
  }

  return (
    <div className={styles.root}>
      {values.map((value) => (
        <span key={value} className={styles.tag}>
          {value}
          <button type="button" className={styles.remove} aria-label={`Убрать «${value}»`} onClick={() => onChange(values.filter((item) => item !== value))}>
            <Icon as={X} size={12} />
          </button>
        </span>
      ))}
      <input id={id} className={styles.input} value={draft} placeholder={placeholder} onChange={(event) => setDraft(event.target.value)} onKeyDown={commit} />
    </div>
  )
}
