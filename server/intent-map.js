// Caregiver WhatsApp text (no em dash, no colon in bodies).
export const INTENTS = {
  help: { message: 'I need help. Please check on me as soon as you can.' },
  hungry: { message: 'I am hungry. Please bring food when you can.' },
  thirsty: {
    message: 'I am thirsty. Please bring something to drink when you can.',
  },
  sick: {
    message:
      'I am not feeling well and may need assistance. Please check on me.',
  },
}

export function getIntentDefinition(intent) {
  const k = intent.trim().toLowerCase()
  return INTENTS[k] ?? null
}

export function buildMessageBody(intent) {
  const def = getIntentDefinition(intent)
  return def?.message ?? null
}
