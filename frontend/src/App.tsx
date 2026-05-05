import { useCallback, useEffect, useRef, useState } from 'react';
import {
  App as AntdApp,
  Alert,
  Button,
  Card,
  InputNumber,
  Select,
  Space,
  Spin,
  Switch,
  Typography,
  theme,
} from 'antd';
import {
  DownloadOutlined,
  ShoppingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  downloadJobUrl,
  fetchJob,
  fetchMeta,
  startJob,
  type JobStatus,
  type MetaResponse,
} from './api';

type PanelTone = 'default' | 'running' | 'done' | 'error';

export default function App() {
  const { token } = theme.useToken();
  const { message } = AntdApp.useApp();
  const [meta, setMeta] = useState<MetaResponse | null>(null);
  const [metaErr, setMetaErr] = useState<string | null>(null);
  const [maxProducts, setMaxProducts] = useState(30);
  const [browserChannel, setBrowserChannel] = useState('auto');
  /** 关闭后不生成 tech_sheets，不调用通义万相 / MiniMax */
  const [generateTechSheets, setGenerateTechSheets] = useState(true);
  const [busy, setBusy] = useState(false);
  const [panelTone, setPanelTone] = useState<PanelTone>('default');
  const [statusText, setStatusText] = useState('就绪。点击下方按钮开始。');
  const [downloadJobId, setDownloadJobId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const runningRef = useRef(false);

  useEffect(() => {
    fetchMeta()
      .then(setMeta)
      .catch((e: unknown) => {
        const msg = String(e);
        const likelyDown =
          /failed to fetch|networkerror|load failed|fetch/i.test(msg) ||
          msg.includes('ECONNREFUSED');
        if (likelyDown) {
          setMetaErr(
            '无法连接后端（代理目标 127.0.0.1:8765）。请先在项目根目录另开终端启动 API：\n\n' +
              '  pip install -r requirements-web.txt\n' +
              '  python web/app.py\n\n' +
              '启动后再刷新本页。详情：' +
              msg
          );
        } else {
          setMetaErr(msg);
        }
      });
  }, []);

  const stopPoll = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const runJob = useCallback(
    async (mode: 'tshirts_hd' | 'all_hd') => {
      if (busy || runningRef.current) return;
      const n = Number(maxProducts);
      if (Number.isNaN(n) || n < 0) {
        message.warning('请输入有效的最多件数（≥0）');
        return;
      }
      runningRef.current = true;
      setBusy(true);
      setDownloadJobId(null);
      setPanelTone('running');
      setStatusText('任务已提交，Playwright 运行中（可能较久）…');
      stopPoll();

      try {
        const started = await startJob({
          mode,
          max_products: n,
          browser_channel: browserChannel,
          generate_tech_sheets: generateTechSheets,
        });
        const { job_id, collection_url } = started;
        if (started.generate_tech_sheets !== undefined) {
          const mismatch = started.generate_tech_sheets !== generateTechSheets;
          if (mismatch) {
            message.warning(
              `后端记录的打版图开关为 ${String(started.generate_tech_sheets)}，与当前开关不一致，请确认已重启 web/app.py 并硬刷新前端。`
            );
          }
        }
        setStatusText(
          `job: ${job_id}\n工作目录: ${started.work_folder ?? '(未知)'}\n集合: ${collection_url}\n打版图: ${String(started.generate_tech_sheets ?? generateTechSheets)}\n轮询状态中…`
        );

        const pollOnce = async (): Promise<boolean> => {
          const j: JobStatus = await fetchJob(job_id);
          if (j.status === 'running') return true;

          stopPoll();
          runningRef.current = false;
          setBusy(false);
          if (j.status === 'done') {
            setPanelTone('done');
            setStatusText(
              [
                '任务已完成。',
                `工作目录: ${j.work_folder ?? '—'}`,
                `模式: ${j.mode ?? '—'}`,
                j.generate_tech_sheets !== undefined
                  ? `打版图: ${j.generate_tech_sheets ? '已尝试生成' : '未生成（仅商品图）'}`
                  : null,
                '请点击下方「下载 ZIP」获取压缩包。',
              ]
                .filter(Boolean)
                .join('\n')
            );
            setDownloadJobId(job_id);
          } else {
            setPanelTone('error');
            setStatusText(`${j.error || '失败'}\n\n${(j.log || '').slice(-6000)}`);
            setDownloadJobId(null);
          }
          return false;
        };

        const stillRunning = await pollOnce();
        if (stillRunning) {
          pollRef.current = setInterval(() => {
            void pollOnce();
          }, 2000);
        }
      } catch (e) {
        stopPoll();
        runningRef.current = false;
        setBusy(false);
        setPanelTone('error');
        setStatusText(String(e));
      }
    },
    [busy, browserChannel, generateTechSheets, maxProducts, message, stopPoll]
  );

  useEffect(() => () => stopPoll(), [stopPoll]);

  const statusBorder =
    panelTone === 'running'
      ? token.colorInfoBorder
      : panelTone === 'done'
        ? token.colorSuccessBorder
        : panelTone === 'error'
          ? token.colorErrorBorder
          : token.colorBorderSecondary;

  return (
    <div className="app-shell">
      <Spin
        spinning={busy}
        fullscreen
        description={
          <>
            下载任务进行中，请勿关闭或刷新页面。
            <br />
            正在操作浏览器抓取商品图。
          </>
        }
      />

      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <div>
          <Typography.Title level={2} style={{ marginBottom: 0 }}>
            Supreme 商品高清打板图
          </Typography.Title>
        </div>

        <Card title="任务参数" variant="borderless">
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <div>
              <Typography.Text type="secondary">最多下载商品数（0 = 不限制）</Typography.Text>
              <InputNumber
                min={0}
                value={maxProducts}
                disabled={busy}
                onChange={(v) => setMaxProducts(typeof v === 'number' ? v : 0)}
                style={{ width: '100%', maxWidth: 280, marginTop: 8, display: 'block' }}
              />
            </div>
            <div>
              <Typography.Text type="secondary">浏览器通道</Typography.Text>
              <Select
                value={browserChannel}
                disabled={busy}
                onChange={setBrowserChannel}
                options={[
                  { value: 'auto', label: 'auto' },
                  { value: 'chrome', label: 'chrome' },
                  { value: 'msedge', label: 'msedge' },
                  { value: 'chromium', label: 'chromium' },
                ]}
                style={{ width: '100%', maxWidth: 280, marginTop: 8, display: 'block' }}
              />
            </div>

            <div>
              <Space align="center" wrap>
                <Switch
                  checked={generateTechSheets}
                  disabled={busy}
                  onChange={setGenerateTechSheets}
                  checkedChildren="生成打板图"
                  unCheckedChildren="仅高清图"
                />
                <Typography.Text type="secondary">
                  关闭后不生成打板文件，不调用通义万相与 MiniMax
                </Typography.Text>
              </Space>
            </div>

            <Space wrap>
              <Button
                type="primary"
                icon={<ShoppingOutlined />}
                disabled={busy}
                onClick={() => void runJob('tshirts_hd')}
              >
                下载 T-Shirts 高清图
              </Button>
              <Button
                type="primary"
                ghost
                icon={<ThunderboltOutlined />}
                disabled={busy}
                onClick={() => void runJob('all_hd')}
              >
                下载「全部分类」高清图
              </Button>
            </Space>
          </Space>
        </Card>

        <Alert
          type="warning"
          showIcon
          message="仅供本地调试"
          description="请遵守 Supreme 网站条款与适用法律。"
        />

        {metaErr && (
          <Alert type="error" showIcon message="元数据加载失败" description={metaErr} />
        )}
        {meta && !metaErr && (
          <Alert
            type="info"
            showIcon
            message="集合 URL（来自后端 /api/meta）"
            description={
              <pre className="status-pre" style={{ margin: 0 }}>
                T-Shirts: {meta.tshirts_url}
                {'\n'}
                All: {meta.all_url}
                {'\n'}
                supreme_shop_common: {meta.supreme_shop_common}
                {'\n'}
                {`打版图: TECH_SHEET_PROVIDER=${meta.tech_sheet_provider ?? 'auto'} | 通义万相: ${meta.dashscope_tech_sheets ? '已配置' : '未配置'} | MiniMax: ${meta.minimax_tech_sheets ? '已配置' : '未配置'}`}
              </pre>
            }
          />
        )}
        {!meta && !metaErr && (
          <Alert type="info" showIcon message="正在加载 /api/meta …" />
        )}

        <Card
          title="运行状态"
          variant="borderless"
          styles={{
            body: {
              border: `1px solid ${statusBorder}`,
              borderRadius: token.borderRadiusLG,
            },
          }}
        >
          <p className="status-pre" role="status">
            {statusText}
          </p>
          {downloadJobId && (
            <Button
              type="link"
              href={downloadJobUrl(downloadJobId)}
              icon={<DownloadOutlined />}
              disabled={busy}
              style={{ paddingLeft: 0, marginTop: 8 }}
            >
              下载 ZIP
            </Button>
          )}
        </Card>
      </Space>
    </div>
  );
}
