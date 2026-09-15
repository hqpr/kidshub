<script setup>
import { computed, onMounted, ref, watch } from 'vue'

const path = window.location.pathname.replace(/^\/+|\/+$/g, '')
const adminPath = import.meta.env.VITE_ADMIN_PATH
const pathParts = path.split('/')
const statsSlug = pathParts[0] === adminPath && pathParts[1] === 'stats' ? pathParts[2] || '' : ''
const isStats = Boolean(statsSlug) && pathParts.length === 3
const isAdmin = path === adminPath
const isManager = isAdmin || isStats
const slug = isManager ? '' : path
const adminKey = ref(sessionStorage.getItem('adminKey') || '')
const authenticated = ref(false)
const polls = ref([])
const poll = ref(null)
const stats = ref(null)
const selectedPoll = ref('')
const currentStep = ref(0)
const answers = ref({})
const name = ref('')
const submitted = ref(false)
const loading = ref(false)
const message = ref('')
const defaultThankYou = 'Дякуємо! Ваші відповіді збережено.'
const editMeta = ref({ slug: '', title: '', description: '', thank_you_text: defaultThankYou })
const editingQuestionId = ref(null)
const questionDraft = ref(null)
const addingQuestion = ref(false)

const newQuestion = () => ({ prompt: '', description: '', response_type: 'single', optionsText: 'Так\nНі\nЩе думаю', ratingMax: 5 })
const form = ref({ slug: '', title: '', description: '', thank_you_text: defaultThankYou, questions: [newQuestion()] })
const typeLabels = { single: 'Один варіант', multiple: 'Кілька варіантів', text: 'Текстова відповідь', rating: 'Оцінка + коментар' }
const usesOptions = type => type === 'single' || type === 'multiple'
const publicUrl = computed(() => selectedPoll.value ? `${window.location.origin}/${selectedPoll.value}/` : '')
const reportUrl = computed(() => selectedPoll.value ? `/${adminPath}/stats/${selectedPoll.value}/` : '')
const reportPoll = computed(() => polls.value.find(item => item.slug === selectedPoll.value))
const namedPercent = computed(() => stats.value?.total ? Math.round(stats.value.named * 100 / stats.value.total) : 0)
const anonymousPercent = computed(() => stats.value?.total ? 100 - namedPercent.value : 0)
const donutBackground = computed(() => stats.value?.total ? `conic-gradient(#ef5f4c 0 ${namedPercent.value}%, #82d8c1 ${namedPercent.value}% 100%)` : '#dedbd3')
const topAnswers = computed(() => (stats.value?.questions || []).flatMap((question, index) => {
  if (question.response_type === 'rating' || !question.distribution.length) return []
  const top = question.distribution.reduce((best, item) => item.count > best.count ? item : best)
  return top.count ? [{ question: index + 1, prompt: question.prompt, ...top }] : []
}))
const reportDate = new Intl.DateTimeFormat('uk-UA', { dateStyle: 'long' }).format(new Date())
const currentQuestion = computed(() => poll.value?.questions[currentStep.value])
const progress = computed(() => poll.value ? Math.round((currentStep.value + 1) * 100 / poll.value.questions.length) : 0)
const draftKey = `tyts:draft:${slug}`

function stored(key) {
  try { return localStorage.getItem(key) } catch { return null }
}

function remember(key, value) {
  try { localStorage.setItem(key, value) } catch { return }
}

function storedJson(key) {
  try { return JSON.parse(localStorage.getItem(key) || 'null') } catch { return null }
}

const respondentId = stored('tyts:respondent') || globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`
remember('tyts:respondent', respondentId)

async function request(url, options = {}) {
  const response = await fetch(url, options)
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(data.detail?.[0]?.msg || data.detail || 'Щось пішло не так')
    error.status = response.status
    throw error
  }
  return data
}

async function loadPoll() {
  loading.value = true
  try {
    if (!slug) {
      const latest = await request('/api/latest-poll')
      window.location.replace(`/${latest.slug}/`)
      return
    }
    poll.value = await request(`/api/polls/${slug}`)
    answers.value = Object.fromEntries(poll.value.questions.map(question => [question.id, { selected: [], rating: null, text: '' }]))
    const draft = storedJson(draftKey)
    if (draft?.version === poll.value.created_at) {
      for (const question of poll.value.questions) {
        if (draft.answers?.[question.id]) answers.value[question.id] = draft.answers[question.id]
      }
      name.value = draft.name || ''
      currentStep.value = Math.min(Math.max(Number(draft.currentStep) || 0, 0), poll.value.questions.length - 1)
      submitted.value = draft.completed === true
    }
  } catch (error) {
    message.value = error.message
  } finally {
    loading.value = false
  }
}

function saveDraft() {
  if (!poll.value || submitted.value) return
  remember(draftKey, JSON.stringify({ version: poll.value.created_at, currentStep: currentStep.value, answers: answers.value, name: name.value, completed: false }))
}

function isAnswered(question) {
  const answer = answers.value[question.id]
  if (question.response_type === 'rating') return Number.isInteger(answer?.rating)
  return question.response_type === 'text' ? Boolean(answer?.text?.trim()) : Boolean(answer?.selected?.length)
}

function toggle(question, option) {
  const answer = answers.value[question.id]
  answer.selected = question.response_type === 'single'
    ? [option]
    : answer.selected.includes(option)
      ? answer.selected.filter(item => item !== option)
      : [...answer.selected, option]
}

async function next() {
  message.value = ''
  if (!isAnswered(currentQuestion.value)) {
    message.value = currentQuestion.value.response_type === 'text'
      ? 'Напишіть відповідь, щоб продовжити.'
      : currentQuestion.value.response_type === 'rating'
        ? 'Поставте оцінку, щоб продовжити.'
        : 'Оберіть варіант, щоб продовжити.'
    return
  }
  if (currentStep.value < poll.value.questions.length - 1) {
    currentStep.value += 1
    window.scrollTo({ top: 0, behavior: 'smooth' })
    return
  }
  await submit()
}

function back() {
  message.value = ''
  currentStep.value = Math.max(0, currentStep.value - 1)
}

async function submit() {
  loading.value = true
  try {
    await request(`/api/polls/${slug}/responses`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        respondent_id: respondentId,
        name: name.value,
        answers: poll.value.questions.map(question => ({ question_id: question.id, ...answers.value[question.id] }))
      })
    })
    submitted.value = true
    remember(draftKey, JSON.stringify({ version: poll.value.created_at, completed: true }))
  } catch (error) {
    if (error.status === 409) {
      submitted.value = true
      remember(draftKey, JSON.stringify({ version: poll.value.created_at, completed: true }))
    } else {
      message.value = error.message
    }
  } finally {
    loading.value = false
  }
}

async function login(preferredSlug = '') {
  message.value = ''
  try {
    polls.value = await request('/api/admin/polls', { headers: { 'X-Admin-Key': adminKey.value } })
    const requestedSlug = preferredSlug || statsSlug
    if (isStats && !polls.value.some(item => item.slug === requestedSlug)) throw new Error('Форму не знайдено')
    sessionStorage.setItem('adminKey', adminKey.value)
    authenticated.value = true
    selectedPoll.value = polls.value.some(item => item.slug === requestedSlug) ? requestedSlug : polls.value[0]?.slug || ''
    if (selectedPoll.value) await loadStats()
  } catch (error) {
    message.value = error.message
  }
}

function addFormQuestion() {
  form.value.questions.push(newQuestion())
}

function removeQuestion(index) {
  if (form.value.questions.length > 1) form.value.questions.splice(index, 1)
}

async function createPoll() {
  loading.value = true
  message.value = ''
  try {
    const questions = form.value.questions.map(questionPayload)
    const created = await request('/api/admin/polls', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': adminKey.value },
      body: JSON.stringify({ ...form.value, questions })
    })
    form.value = { slug: '', title: '', description: '', thank_you_text: defaultThankYou, questions: [newQuestion()] }
    await login(created.slug)
    message.value = 'Форма готова — лінк можна надсилати.'
  } catch (error) {
    message.value = error.message
  } finally {
    loading.value = false
  }
}

async function loadStats() {
  if (!selectedPoll.value) return
  stats.value = await request(`/api/admin/polls/${selectedPoll.value}/stats`, { headers: { 'X-Admin-Key': adminKey.value } })
  const item = polls.value.find(pollItem => pollItem.slug === selectedPoll.value)
  if (item) editMeta.value = { slug: item.slug, title: item.title, description: item.description, thank_you_text: item.thank_you_text }
  editingQuestionId.value = null
  addingQuestion.value = false
}

function questionPayload(question) {
  return {
    prompt: question.prompt,
    description: question.description,
    response_type: question.response_type,
    options: question.response_type === 'rating' ? [String(question.ratingMax || 5)] : usesOptions(question.response_type) ? question.optionsText.split('\n') : []
  }
}

function formatRating(value) {
  return Number(value).toFixed(1).replace('.', ',')
}

function ratingMax(question) {
  return Number(question.options?.[0] || question.ratingMax || 5)
}

function startQuestionEdit(question) {
  editingQuestionId.value = question.id
  addingQuestion.value = false
  questionDraft.value = { ...question, optionsText: question.options.join('\n'), ratingMax: ratingMax(question) }
}

async function savePack() {
  try {
    const result = await request(`/api/admin/polls/${selectedPoll.value}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': adminKey.value },
      body: JSON.stringify(editMeta.value)
    })
    await login(result.slug)
    message.value = 'Налаштування форми збережено.'
  } catch (error) {
    message.value = error.message
  }
}

async function removePack() {
  if (!window.confirm('Видалити всю форму разом з усіма відповідями? Цю дію не можна скасувати.')) return
  try {
    await request(`/api/admin/polls/${selectedPoll.value}`, { method: 'DELETE', headers: { 'X-Admin-Key': adminKey.value } })
    await login()
    message.value = 'Форму видалено.'
  } catch (error) {
    message.value = error.message
  }
}

async function saveQuestion() {
  try {
    await request(`/api/admin/questions/${editingQuestionId.value}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': adminKey.value },
      body: JSON.stringify(questionPayload(questionDraft.value))
    })
    await login(selectedPoll.value)
    message.value = 'Питання збережено.'
  } catch (error) {
    message.value = error.message
  }
}

async function removeExistingQuestion(questionId) {
  if (!window.confirm('Видалити це питання та всі відповіді на нього?')) return
  try {
    await request(`/api/admin/questions/${questionId}`, { method: 'DELETE', headers: { 'X-Admin-Key': adminKey.value } })
    await login(selectedPoll.value)
    message.value = 'Питання видалено.'
  } catch (error) {
    message.value = error.message
  }
}

function beginAddQuestion() {
  editingQuestionId.value = null
  addingQuestion.value = true
  questionDraft.value = newQuestion()
}

async function addExistingQuestion() {
  try {
    await request(`/api/admin/polls/${selectedPoll.value}/questions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Admin-Key': adminKey.value },
      body: JSON.stringify(questionPayload(questionDraft.value))
    })
    await login(selectedPoll.value)
    message.value = 'Питання додано.'
  } catch (error) {
    message.value = error.message
  }
}

async function copyLink() {
  await navigator.clipboard.writeText(publicUrl.value)
  message.value = 'Лінк скопійовано.'
}

function printReport() {
  window.print()
}

watch([currentStep, answers, name], saveDraft, { deep: true })
onMounted(() => isManager ? (adminKey.value && login()) : loadPoll())
</script>

<template>
  <main>
    <header v-if="!isStats" class="topbar">
      <a class="brand-logo" href="/"><img src="/kids-hub-logo.png" alt="Kids Hub"></a>
      <span class="tagline"></span>
    </header>

    <section v-if="isManager && !authenticated" class="login-card card">
      <span class="eyebrow">Для своїх</span>
      <h1>Заходьте,<br>тут усе видно.</h1>
      <form @submit.prevent="login()">
        <label>Ключ адміністратора<input v-model="adminKey" type="password" minlength="12" maxlength="128" autocomplete="current-password" autofocus required></label>
        <button>Увійти →</button>
        <p v-if="message" class="error">{{ message }}</p>
      </form>
    </section>

    <section v-else-if="isStats && stats && reportPoll" class="report-shell">
      <nav class="report-toolbar">
        <a :href="`/${adminPath}/`">← До адмінки</a>
        <button type="button" @click="printReport">Друк / PDF</button>
      </nav>

      <header class="report-hero">
        <div>
          <span class="brand-logo report-logo"><img src="/kids-hub-logo.png" alt="Kids Hub"></span>
          <span class="report-kicker">Підсумки опитування · {{ reportDate }}</span>
          <h1>{{ reportPoll.title }}</h1>
          <p v-if="reportPoll.description">{{ reportPoll.description }}</p>
        </div>
        <div class="report-seal"><strong>{{ stats.total }}</strong><span>людей<br>проголосували</span></div>
      </header>

      <section class="report-kpis">
        <article class="report-kpi coral-kpi">
          <span>Участь</span>
          <strong>{{ stats.total }}</strong>
          <p>завершених анкет</p>
        </article>
        <article class="report-kpi">
          <span>Обсяг</span>
          <strong>{{ stats.questions.length }}</strong>
          <p>питань у формі</p>
        </article>
        <article class="report-kpi identity-kpi">
          <div class="donut" :style="{ background: donutBackground }"><b>{{ namedPercent }}%</b></div>
          <div><span>Авторство</span><p><i class="coral-dot"></i>{{ stats.named }} з іменем</p><p><i class="mint-dot"></i>{{ stats.total - stats.named }} анонімно · {{ anonymousPercent }}%</p></div>
        </article>
      </section>

      <section v-if="topAnswers.length" class="report-section">
        <div class="report-section-head"><span>01</span><div><h2>Що обирали найчастіше</h2><p>Лідер кожного питання з варіантами відповіді</p></div></div>
        <div class="insight-grid">
          <article v-for="item in topAnswers" :key="item.question" class="insight-card">
            <span>Питання {{ item.question }}</span>
            <h3>{{ item.option }}</h3>
            <div><strong>{{ item.percent }}%</strong><small>{{ item.count }} виборів</small></div>
          </article>
        </div>
      </section>

      <section class="report-section">
        <div class="report-section-head"><span>02</span><div><h2>Результати за питаннями</h2><p>Розподіл усіх отриманих відповідей</p></div></div>
        <div class="report-questions">
          <article v-for="(question, index) in stats.questions" :key="question.id" class="report-question" :class="{ wide: ['text', 'rating'].includes(question.response_type) }">
            <div class="report-question-head">
              <span>{{ String(index + 1).padStart(2, '0') }}</span>
              <div><h3>{{ question.prompt }}</h3><p>{{ question.answer_count }} відповідей<span v-if="question.response_type === 'multiple'"> · можна було обрати кілька</span></p></div>
            </div>
            <div v-if="question.response_type === 'rating' && question.average_rating !== null" class="rating-summary report-rating-summary"><strong>{{ formatRating(question.average_rating) }}</strong><span>середній бал<br>із {{ ratingMax(question) }}</span></div>
            <div v-if="question.distribution.length" class="report-bars">
              <div v-for="(item, itemIndex) in question.distribution" :key="item.option" class="report-bar-row">
                <div class="report-bar-label"><span>{{ item.option }}</span><strong>{{ item.percent }}% <small>{{ item.count }}</small></strong></div>
                <div class="report-bar-track"><i :class="`chart-color-${itemIndex % 4}`" :style="{ width: `${item.percent}%` }"></i></div>
              </div>
            </div>
            <div v-if="question.answers.length" class="report-quotes" :class="{ 'rating-quotes': question.response_type === 'rating' }">
              <blockquote v-for="(item, answerIndex) in question.answers.slice(0, 8)" :key="answerIndex"><p>{{ item.answer }}</p><cite><template v-if="item.rating">{{ item.rating }}/{{ ratingMax(question) }} · </template>{{ item.name || 'Анонімно' }}</cite></blockquote>
              <p v-if="question.answers.length > 8" class="more-answers">Ще {{ question.answers.length - 8 }} відповідей у повній статистиці</p>
            </div>
            <p v-if="!question.distribution.length && !question.answers.length" class="report-empty">Відповідей поки немає</p>
          </article>
        </div>
      </section>

      <footer class="report-footer"><span class="brand-logo report-footer-logo"><img src="/kids-hub-logo.png" alt="Kids Hub"></span><p>Kids Hub · {{ reportDate }}</p></footer>
    </section>

    <section v-else-if="isStats" class="center-state">{{ message || 'Готую статистику…' }}</section>

    <section v-else-if="isAdmin" class="admin-shell">
      <div class="admin-heading">
        <div><span class="eyebrow">Пульт керування</span><h1>Питай.<br>Дізнавайся.</h1></div>
        <div class="burst">{{ polls.reduce((sum, item) => sum + item.response_count, 0) }}<small>анкет</small></div>
      </div>

      <div class="admin-grid">
        <form class="card create-card" @submit.prevent="createPoll">
          <div class="section-title"><span>01</span><h2>Нова форма</h2></div>
          <label>Назва форми<input v-model="form.title" maxlength="120" placeholder="Літня подорож" required></label>
          <label>Короткий опис<textarea v-model="form.description" rows="2" maxlength="500" placeholder="Додайте трохи контексту"></textarea></label>
          <label>Текст після завершення<textarea v-model="form.thank_you_text" rows="3" minlength="3" maxlength="1000" required></textarea></label>
          <label>Slug<input v-model="form.slug" pattern="[a-z0-9-]+" placeholder="summer" required></label>

          <div class="questions-heading"><h3>Питання</h3><b>{{ form.questions.length }}</b></div>
          <article v-for="(question, index) in form.questions" :key="index" class="question-editor">
            <div class="question-editor-head"><strong>{{ String(index + 1).padStart(2, '0') }}</strong><button v-if="form.questions.length > 1" type="button" class="remove-button" @click="removeQuestion(index)">Видалити</button></div>
            <label>Питання<input v-model="question.prompt" maxlength="200" placeholder="Куди хочете поїхати?" required></label>
            <label>Пояснення <small>необов’язково</small><input v-model="question.description" maxlength="500" placeholder="Уточніть, якщо потрібно"></label>
            <label>Тип<select v-model="question.response_type"><option v-for="(label, key) in typeLabels" :key="key" :value="key">{{ label }}</option></select></label>
            <label v-if="question.response_type === 'rating'">Шкала<select v-model.number="question.ratingMax"><option :value="5">Від 1 до 5</option><option :value="10">Від 1 до 10</option></select></label>
            <label v-if="usesOptions(question.response_type)">Варіанти — кожен з нового рядка<textarea v-model="question.optionsText" rows="4" required></textarea></label>
          </article>
          <button type="button" class="add-button" @click="addFormQuestion">+ Додати питання</button>
          <button class="create-button" :disabled="loading">{{ loading ? 'Створюю…' : 'Створити форму →' }}</button>
        </form>

        <section class="card stats-card">
          <div class="section-title"><span>02</span><h2>Усі відповіді</h2></div>
          <template v-if="polls.length">
            <div class="stats-tools">
              <select v-model="selectedPoll" @change="loadStats"><option v-for="item in polls" :key="item.slug" :value="item.slug">{{ item.title }}</option></select>
              <a class="report-button" :href="reportUrl">Звіт</a>
              <button class="icon-button" type="button" title="Скопіювати лінк" @click="copyLink">↗</button>
            </div>
            <div v-if="stats" class="stats-body">
              <form class="manage-pack" @submit.prevent="savePack">
                <div class="manage-head"><h3>Налаштування форми</h3><button type="button" class="danger-link" @click="removePack">Видалити всю</button></div>
                <label>Назва<input v-model="editMeta.title" maxlength="120" required></label>
                <label>Опис<textarea v-model="editMeta.description" rows="2" maxlength="500"></textarea></label>
                <label>Текст після завершення<textarea v-model="editMeta.thank_you_text" rows="3" minlength="3" maxlength="1000" required></textarea></label>
                <label>Slug<input v-model="editMeta.slug" pattern="[a-z0-9-]+" maxlength="48" required></label>
                <button>Зберегти форму</button>
              </form>
              <div class="numbers"><div><strong>{{ stats.total }}</strong><span>завершили</span></div><div><strong>{{ stats.named }}</strong><span>з іменем</span></div></div>
              <div class="question-stats">
                <article v-for="(question, index) in stats.questions" :key="question.id" class="question-stat">
                  <div class="stat-head"><span class="stat-number">Питання {{ index + 1 }} · {{ question.answer_count }} відповідей</span><button type="button" class="edit-link" @click="startQuestionEdit(question)">Редагувати</button></div>
                  <form v-if="editingQuestionId === question.id" class="inline-editor" @submit.prevent="saveQuestion">
                    <label>Питання<input v-model="questionDraft.prompt" maxlength="200" required></label>
                    <label>Пояснення<input v-model="questionDraft.description" maxlength="500"></label>
                    <label>Тип<select v-model="questionDraft.response_type"><option v-for="(label, key) in typeLabels" :key="key" :value="key">{{ label }}</option></select></label>
                    <label v-if="questionDraft.response_type === 'rating'">Шкала<select v-model.number="questionDraft.ratingMax"><option :value="5">Від 1 до 5</option><option :value="10">Від 1 до 10</option></select></label>
                    <label v-if="usesOptions(questionDraft.response_type)">Варіанти<textarea v-model="questionDraft.optionsText" rows="4" required></textarea></label>
                    <div class="editor-actions"><button>Зберегти</button><button type="button" class="danger-button" @click="removeExistingQuestion(question.id)">Видалити питання</button></div>
                  </form>
                  <template v-else>
                    <h3>{{ question.prompt }}</h3>
                    <div v-if="question.response_type === 'rating' && question.average_rating !== null" class="rating-summary"><strong>{{ formatRating(question.average_rating) }}</strong><span>середній бал із {{ ratingMax(question) }}</span></div>
                    <div v-if="question.distribution.length" class="bars">
                      <div v-for="item in question.distribution" :key="item.option" class="bar-row">
                        <div class="bar-label"><span>{{ item.option }}</span><b>{{ item.count }} · {{ item.percent }}%</b></div>
                        <div class="bar-track"><i :style="{ width: `${item.percent}%` }"></i></div>
                      </div>
                    </div>
                    <div v-if="question.answers.length" class="text-answers">
                      <article v-for="(item, answerIndex) in question.answers" :key="answerIndex"><p>{{ item.answer }}</p><span><template v-if="item.rating">{{ item.rating }}/{{ ratingMax(question) }} · </template>{{ item.name || 'Анонімно' }}</span></article>
                    </div>
                    <p v-if="!question.distribution.length && !question.answers.length" class="empty">Поки без відповідей.</p>
                  </template>
                </article>
              </div>
              <form v-if="addingQuestion" class="inline-editor add-existing" @submit.prevent="addExistingQuestion">
                <h3>Нове питання</h3>
                <label>Питання<input v-model="questionDraft.prompt" maxlength="200" required></label>
                <label>Пояснення<input v-model="questionDraft.description" maxlength="500"></label>
                <label>Тип<select v-model="questionDraft.response_type"><option v-for="(label, key) in typeLabels" :key="key" :value="key">{{ label }}</option></select></label>
                <label v-if="questionDraft.response_type === 'rating'">Шкала<select v-model.number="questionDraft.ratingMax"><option :value="5">Від 1 до 5</option><option :value="10">Від 1 до 10</option></select></label>
                <label v-if="usesOptions(questionDraft.response_type)">Варіанти<textarea v-model="questionDraft.optionsText" rows="4" required></textarea></label>
                <div class="editor-actions"><button>Додати</button><button type="button" class="back-button" @click="addingQuestion = false">Скасувати</button></div>
              </form>
              <button v-else type="button" class="add-existing-button" @click="beginAddQuestion">+ Додати питання до форми</button>
              <a class="share-link" :href="`/${selectedPoll}/`" target="_blank">{{ publicUrl }}</a>
            </div>
          </template>
          <p v-else class="empty">Створіть першу форму ліворуч.</p>
        </section>
      </div>
      <p v-if="message" class="toast" @click="message = ''">{{ message }}</p>
    </section>

    <section v-else-if="loading && !poll" class="center-state">Завантажую…</section>
    <section v-else-if="submitted" class="poll-wrap success">
      <div class="success-mark">✓</div>
      <span class="eyebrow">Усе готово</span>
      <h1>Готово.</h1>
      <p>{{ poll.thank_you_text }}</p>
    </section>
    <section v-else-if="poll && currentQuestion" class="poll-wrap step-screen">
      <div class="step-meta"><span>{{ poll.title }}</span><b>{{ currentStep + 1 }} / {{ poll.questions.length }}</b></div>
      <div class="progress-track"><i :style="{ width: `${progress}%` }"></i></div>
      <form class="answer-form" @submit.prevent="next">
        <span class="eyebrow">Питання {{ currentStep + 1 }}</span>
        <h1>{{ currentQuestion.prompt }}</h1>
        <p v-if="currentQuestion.description" class="lead">{{ currentQuestion.description }}</p>
        <div v-if="currentQuestion.response_type === 'rating'" class="rating-answer">
          <div class="rating-value"><strong>{{ answers[currentQuestion.id].rating || '—' }}</strong><span>із {{ ratingMax(currentQuestion) }}</span></div>
          <input v-model.number="answers[currentQuestion.id].rating" class="rating-slider" :class="{ untouched: answers[currentQuestion.id].rating === null }" type="range" min="1" :max="ratingMax(currentQuestion)" step="1" aria-label="Оцінка" @input="message = ''">
          <div class="rating-hints"><span>1 · Не сподобалось</span><span>{{ ratingMax(currentQuestion) }} · Чудово</span></div>
          <label class="big-answer rating-comment">Коментар <small>необов’язково</small><textarea v-model="answers[currentQuestion.id].text" rows="4" maxlength="1000" placeholder="Що вплинуло на вашу оцінку?"></textarea></label>
        </div>
        <div v-else-if="currentQuestion.response_type !== 'text'" class="choices">
          <button v-for="(option, index) in currentQuestion.options" :key="option" type="button" class="choice" :class="{ chosen: answers[currentQuestion.id].selected.includes(option) }" @click="toggle(currentQuestion, option)">
            <span>{{ String(index + 1).padStart(2, '0') }}</span><b>{{ option }}</b><i>{{ answers[currentQuestion.id].selected.includes(option) ? '✓' : '→' }}</i>
          </button>
        </div>
        <label v-else class="big-answer">Ваша відповідь<textarea v-model="answers[currentQuestion.id].text" rows="5" maxlength="1000" placeholder="Пишіть як є…" autofocus></textarea></label>
        <label v-if="currentStep === poll.questions.length - 1" class="name-field">Як вас звати? <small>необов’язково</small><input v-model="name" maxlength="80" placeholder="Можна залишитись анонімно"></label>
        <div class="step-actions">
          <button v-if="currentStep" type="button" class="back-button" @click="back">← Назад</button>
          <button class="submit-answer" :disabled="loading">{{ loading ? 'Зберігаю…' : currentStep === poll.questions.length - 1 ? 'Завершити →' : 'Далі →' }}</button>
        </div>
        <p v-if="message" class="error">{{ message }}</p>
      </form>
    </section>
    <section v-else class="center-state"><h1>Ой.</h1><p>{{ message || 'Додайте slug опитування до адреси.' }}</p></section>
  </main>
</template>
