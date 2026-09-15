// code:web-component-008:journey-transition-rules

import Link from 'next/link';
import { JOURNEY_TRANSITIONS } from '@/lib/journey-engine';

export function JourneyTransitionRules() {
  return (
    <div className="card" style={{ marginTop: '24px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <div>
          <h2 style={{ fontSize: '16px', fontWeight: 700, margin: 0 }}>
            ⚡ Journey Transition Rules
          </h2>
          <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: '4px 0 0 0' }}>
            Xem danh sách Seekers tương ứng theo từng giai đoạn đích trên trang /seekers.
          </p>
        </div>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              <th>From Stage</th>
              <th>To Stage</th>
              <th>Trigger</th>
              <th>Condition</th>
              <th>Action</th>
              <th aria-label="Mở danh sách seekers">Thao tác</th>
            </tr>
          </thead>
          <tbody>
            {JOURNEY_TRANSITIONS.map((t, i) => (
              <tr key={i}>
                <td>
                  <span className="badge badge-indigo">{t.fromStage}</span>
                </td>
                <td>
                  <Link
                    href={`/seekers?journeyStage=${encodeURIComponent(t.toStage)}`}
                    className="journey-stage-link"
                    title={`Lọc danh sách seekers theo giai đoạn đích: ${t.toStage}`}
                    aria-label={`Giai đoạn đích: ${t.toStage}`}
                  >
                    <span className="badge badge-emerald">{t.toStage}</span>
                  </Link>
                </td>
                <td><span className="badge badge-amber">{t.triggerType}</span></td>
                <td>{t.condition}</td>
                <td style={{ fontSize: '12px' }}>{t.action}</td>
                <td>
                  <Link
                    href={`/seekers?journeyStage=${encodeURIComponent(t.toStage)}`}
                    className="journey-rule-link"
                    title={`Xem seekers ở giai đoạn ${t.toStage}`}
                    aria-label={`Xem seekers ở giai đoạn ${t.toStage}`}
                  >
                    Xem seekers ở giai đoạn {t.toStage} →
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
