import { existsSync } from 'node:fs'
import path from 'node:path'
import dotenv from 'dotenv'

for (const file of [
  path.join(process.cwd(), '.env'),
  path.join(process.cwd(), '..', '.env'),
]) {
  if (existsSync(file)) {
    dotenv.config({ path: file })
    break
  }
}
