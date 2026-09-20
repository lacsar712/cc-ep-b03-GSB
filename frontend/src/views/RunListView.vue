<template>
  <div class="page">
    <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px">
      <div>
        <h1 style="margin-bottom: 4px">实验 Run 列表</h1>
        <p class="muted" style="margin-top: 0">按项目与状态筛选投影视图</p>
      </div>
      <n-button v-if="auth.role === 'researcher'" type="primary" @click="$router.push('/runs/new')">
        新建 Run
      </n-button>
    </div>

    <div class="card" style="margin-bottom: 16px">
      <div class="grid-2">
        <n-form-item label="项目" :show-feedback="false">
          <n-input v-model:value="project" clearable placeholder="例如 protein-folding" />
        </n-form-item>
        <n-form-item label="状态" :show-feedback="false">
          <n-select
            v-model:value="status"
            clearable
            :options="statusOptions"
            placeholder="全部"
          />
        </n-form-item>
      </div>
      <div style="display: flex; align-items: center; justify-content: space-between; margin-top: 8px; gap: 12px; flex-wrap: wrap">
        <n-button @click="load">筛选</n-button>
        <n-space align="center" :size="8">
          <n-switch v-model:value="includeArchived" size="small" @update:value="load" />
          <span class="muted">含归档</span>
        </n-space>
      </div>
    </div>

    <div class="card">
      <n-data-table :columns="columns" :data="rows" :loading="loading" :bordered="false" />
    </div>
  </div>
</template>

<script setup>
import { h, onMounted, ref } from 'vue'
import { NButton, NSpace, NSwitch, NTag, useDialog, useMessage } from 'naive-ui'
import { useRouter } from 'vue-router'
import { archiveRun, listRuns } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const message = useMessage()
const dialog = useDialog()
const rows = ref([])
const loading = ref(false)
const project = ref('')
const status = ref(null)
const includeArchived = ref(false)

const statusOptions = [
  { label: '进行中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '已中止', value: 'aborted' },
]

const statusMap = {
  running: { type: 'info', label: '进行中' },
  completed: { type: 'success', label: '已完成' },
  aborted: { type: 'warning', label: '已中止' },
}

const columns = [
  { title: '项目', key: 'project' },
  {
    title: '名称',
    key: 'name',
    render(row) {
      if (!row.archived) return row.name
      return h('span', { style: 'display:inline-flex;align-items:center;gap:8px' }, [
        row.name,
        h(
          NTag,
          { type: 'default', size: 'small', bordered: false },
          { default: () => '已归档' },
        ),
      ])
    },
  },
  {
    title: '状态',
    key: 'status',
    render(row) {
      const m = statusMap[row.status] || { type: 'default', label: row.status }
      return h(NTag, { type: m.type, size: 'small' }, { default: () => m.label })
    },
  },
  { title: '版本', key: 'version', width: 70 },
  {
    title: '开始时间',
    key: 'started_at',
    render(row) {
      return new Date(row.started_at).toLocaleString()
    },
  },
  {
    title: '操作',
    key: 'actions',
    render(row) {
      const buttons = [
        h(NButton, { size: 'tiny', onClick: () => router.push(`/runs/${row.id}`) }, { default: () => '详情' }),
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => router.push(`/runs/${row.id}/events`) }, { default: () => '事件' }),
        h(NButton, { size: 'tiny', quaternary: true, onClick: () => router.push(`/runs/${row.id}/lineage`) }, { default: () => '血缘' }),
      ]
      if (
        auth.role === 'researcher' &&
        row.status === 'completed' &&
        !row.archived
      ) {
        buttons.push(
          h(
            NButton,
            {
              size: 'tiny',
              quaternary: true,
              type: 'warning',
              onClick: () => confirmArchive(row),
            },
            { default: () => '归档' },
          ),
        )
      }
      return h(
        'div',
        { style: 'display:flex;gap:8px;flex-wrap:wrap' },
        buttons,
      )
    },
  },
]

function confirmArchive(row) {
  dialog.warning({
    title: '归档 Run',
    content: `确认归档「${row.name}」？归档后默认列表不再显示，可通过“含归档”开关查看；事件流水保留且不可删除。`,
    positiveText: '确认归档',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await archiveRun(row.id, { expected_version: row.version })
        message.success('已归档')
        await load()
      } catch (e) {
        message.error(e.message || '归档失败')
      }
    },
  })
}

async function load() {
  loading.value = true
  try {
    const params = {}
    if (project.value.trim()) params.project = project.value.trim()
    if (status.value) params.status = status.value
    if (includeArchived.value) params.include_archived = true
    rows.value = await listRuns(params)
  } catch (e) {
    message.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
