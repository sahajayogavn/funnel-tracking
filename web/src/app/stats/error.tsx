'use client';
export default function Error({ reset }: { reset: () => void }) {
  return <div role="alert" className="card" style={{ padding: 32 }}><h1>Không thể tải thống kê</h1><p style={{ margin: '16px 0' }}>Vui lòng thử lại để tải dữ liệu.</p><button className="btn btn-primary" onClick={reset}>Thử lại</button></div>;
}
