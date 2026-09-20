import Link from 'next/link';
import { getPeriodStats, statsRange, type Ranking } from '@/lib/stats';
import { getAllSeekers, getDashboardStats } from '@/lib/queries';
import { JourneyFlow } from '@/components/journey-flow';
import { JourneyTransitionRules } from '@/components/journey-transition-rules';
import './stats.css';
import { StatsFilters } from './stats-filters';
import type { DateRange } from '@/lib/funnel-filters';

export const dynamic = 'force-dynamic';
const number = (value: number) => value.toLocaleString('vi-VN');

function Cards({ items }: { items: { label: string; value: number; note?: string }[] }) {
  return <div className="stats-cards">{items.map(item => <div className="card stats-metric" key={item.label}>
    <div className="stats-caption">{item.label}</div><strong>{number(item.value)}</strong>
    {item.note && <small>{item.note}</small>}
  </div>)}</div>;
}
function Ranks({ title, rows }: { title: string; rows: Ranking[] }) {
  const max = Math.max(1, ...rows.map(row => row.count));
  return <section className="card stats-panel"><h2>{title}</h2>
    {rows.length ? <ol className="stats-ranking">{rows.map(row => <li key={row.label}>
      <div><span>{row.label}</span><strong>{number(row.count)}</strong></div>
      <div className="stats-track"><div style={{ width: `${row.count / max * 100}%` }} /></div>
    </li>)}</ol> : <p className="stats-note">Chưa có tin nhắn trong khoảng thời gian này.</p>}
  </section>;
}
export default async function StatsPage({ searchParams }: { searchParams: Promise<{ days?: string }> }) {
  const { days } = await searchParams;
  const range = await statsRange(days);
  const stats = await getPeriodStats(range);
  const overview = await getDashboardStats();
  const seekers = await getAllSeekers();
  const totals = stats.models.reduce((sum, row) => ({ calls: sum.calls + row.calls, input: sum.input + row.input, output: sum.output + row.output, missing: sum.missing + row.missing }), { calls: 0, input: 0, output: 0, missing: 0 });
  const max = Math.max(1, ...stats.series.map(row => row.count));
  return <div className="stats-page">
    <header className="page-header stats-header"><div><h1 className="page-title">Thống kê</h1>
      <p className="page-subtitle">Tin nhắn, hoạt động MAS và mức sử dụng LLM</p></div>
      <StatsFilters dateRange={days === 'all' ? 'all' : `${range.days}d` as DateRange} />
    </header>
    <p className="stats-note">{range.from} → {range.today} · Giờ Việt Nam · Bao gồm hôm nay</p>
    <Cards items={[
      { label: 'Tin nhắn mới từ khách', value: stats.incoming, note: 'Theo thời điểm gửi tin nhắn' },
      { label: 'Hội thoại có tin mới', value: stats.conversations, note: 'Số hội thoại riêng biệt' },
      { label: 'Lượt chạy MAS', value: stats.masRuns, note: 'Trace MAS bắt đầu trong kỳ' },
      { label: 'Token đã ghi nhận', value: totals.input + totals.output, note: `${number(totals.calls)} lượt gọi LLM` },
    ]} />
    <section className="card stats-panel"><h2>Tin nhắn theo ngày</h2><p className="stats-note">Số tin nhắn từ khách trong {range.days} ngày gần nhất</p>
      {stats.incoming === 0 && <p className="stats-note">Chưa có tin nhắn trong khoảng thời gian này.</p>}
      <div className="stats-chart" role="img" aria-label={`Biểu đồ ${range.days} ngày, tổng ${number(stats.incoming)} tin nhắn. Chi tiết trong bảng bên dưới.`}>
        <div className="stats-axis"><span>{number(max)}</span><span>{number(Math.round(max / 2))}</span><span>0</span></div>
        <div className="stats-bars">{stats.series.map(row => <div className="stats-bar-slot" key={row.day} title={`${row.day}: ${number(row.count)} tin nhắn`}><div style={{ height: `${row.count / max * 100}%` }} /></div>)}</div>
      </div><div className="stats-chart-dates"><span>{range.from}</span><span>{range.today}</span></div>
      <details><summary>Xem số liệu từng ngày</summary><div className="stats-table-wrap"><table className="stats-table"><thead><tr><th>Ngày</th><th>Tin nhắn</th></tr></thead><tbody>{stats.series.map(row => <tr key={row.day}><td>{row.day}</td><td>{number(row.count)}</td></tr>)}</tbody></table></div></details>
    </section>
    <div className="stats-columns"><Ranks title="Thành phố nhận nhiều tin nhắn" rows={stats.cities} /><Ranks title="Chương trình nhận nhiều tin nhắn" rows={stats.programs} /></div>
    <p className="stats-note">Phân nhóm theo thành phố và chương trình hiện tại của hồ sơ; mỗi tin nhắn được tính một lần. Loại trừ tin của trang, thông báo hệ thống và {number(stats.undated)} tin từ khách chưa xác định được ngày gửi (toàn bộ dữ liệu).</p>
    <section><h2 className="stats-section-title">Fetching & MAS</h2><Cards items={[
      { label: 'Tin nhắn được fetch trong kỳ', value: stats.fetched, note: 'Theo thời điểm lưu vào hệ thống, gồm cả hai chiều' },
      { label: 'Lượt fetch được ghi nhận', value: stats.fetchRuns },
      { label: 'Lượt chạy MAS', value: stats.masRuns, note: 'Gồm chạy thử; không cộng trùng agent trong trace' },
      { label: 'Hành động đã phê duyệt', value: stats.approved, note: 'Theo thời điểm phê duyệt' },
      { label: 'Hành động đã thực thi', value: stats.executed, note: 'Theo thời điểm thực thi' },
      { label: 'Tổng tin nhắn đang lưu', value: stats.totalFetched, note: 'Toàn thời gian · không gồm thông báo hệ thống' },
    ]} /><p className="stats-note">Lượt MAS dựa trên log còn lưu; không tính quyết định thuần quy tắc. Log bị xóa sẽ không còn trong thống kê.</p></section>
    <section className="card stats-panel"><h2>LLM · Tổng hợp token</h2>
      <Cards items={[{ label: 'Input tokens', value: totals.input }, { label: 'Output tokens', value: totals.output }, { label: 'Tổng tokens', value: totals.input + totals.output }, { label: 'Lượt gọi thiếu usage', value: totals.missing, note: `Trên ${number(totals.calls)} lượt gọi` }]} />
      <p className="stats-note">Ước tính mức sử dụng từ token mà nhà cung cấp trả về, trên toàn bộ log trong kỳ. Các lượt thiếu usage không được suy đoán thêm; tổng có thể thấp hơn thực tế. Không gồm quyết định thuần quy tắc.</p>
      {stats.models.length ? <div className="stats-table-wrap"><table className="stats-table"><thead><tr><th>Model</th><th>Lượt gọi</th><th>Input</th><th>Output</th><th>Tổng</th><th>Thiếu usage</th></tr></thead><tbody>{stats.models.map(row => <tr key={row.label}><td>{row.label}</td><td>{number(row.calls)}</td><td>{number(row.input)}</td><td>{number(row.output)}</td><td>{number(row.input + row.output)}</td><td>{number(row.missing)}</td></tr>)}</tbody></table></div> : <p className="stats-note">Chưa có lượt gọi LLM trong khoảng thời gian này.</p>}
      <Link href="/llm" className="stats-link">Xem LLM Observability →</Link>
    </section>
    <section><h2 className="stats-section-title">Tổng quan toàn thời gian</h2><Cards items={[{ label: 'Contacts', value: overview.totalSeekers }, { label: 'DM users', value: overview.totalDMUsers }, { label: 'Bài viết', value: overview.totalPosts }, { label: 'Bình luận', value: overview.totalComments }]} /></section>
    <section id="journey" aria-labelledby="journey-title">
      <h2 id="journey-title" className="stats-section-title">Hành trình contacts · Journey Workflow</h2>
      <p className="stats-note">Tổng {number(seekers.length)} contacts trên toàn bộ hành trình. Mỗi node hiển thị số cộng dồn từ giai đoạn đó trở đi; các đường nối mô tả điều kiện chuyển giai đoạn. Bộ lọc bên dưới chỉ áp dụng cho Journey, độc lập với kỳ thống kê phía trên.</p>
      <JourneyFlow initialSeekers={seekers} />
      <JourneyTransitionRules />
    </section>
  </div>;
}
