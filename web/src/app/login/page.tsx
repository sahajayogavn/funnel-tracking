"use client";

import { useEffect, useState, type FormEvent } from "react";
import styles from "./page.module.css";

const LOCAL_SESSION_KEY = "sahaja_session";

export default function LoginPage() {
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  function continueToApp() {
    const next = new URLSearchParams(window.location.search).get("next");
    const destination = new URL(next || "/", window.location.origin);
    window.location.replace(destination.origin === window.location.origin && destination.pathname !== "/login" ? destination.href : "/");
  }

  useEffect(() => {
    const session = window.localStorage.getItem(LOCAL_SESSION_KEY);
    if (!session) return;
    let active = true;
    fetch("/api/auth/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session }),
    }).then(async (response) => {
      if (!active) return;
      if (response.ok) {
        continueToApp();
        return;
      }
      window.localStorage.removeItem(LOCAL_SESSION_KEY);
    }).catch(() => undefined);
    return () => { active = false; };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setPending(true);
    const data = new FormData(event.currentTarget);
    try {
      const response = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identity: data.get("identity"), technique: data.get("technique") }),
      });
      if (!response.ok) {
        const result = await response.json();
        setError(result.error || "Chưa thể xác thực. Vui lòng thử lại.");
        setPending(false);
        return;
      }
      const result = await response.json();
      window.localStorage.setItem(LOCAL_SESSION_KEY, result.session);
      continueToApp();
    } catch {
      setError("Không thể kết nối. Vui lòng thử lại.");
      setPending(false);
    }
  }

  return (
    <div className={styles.screen}>
      <section className={styles.card} aria-labelledby="login-title" lang="vi">
        <div className={styles.brand}>🪷 Sahaja Yoga VN</div>
        <h1 id="login-title">Chào mừng bạn</h1>
        <p className={styles.intro}>Vui lòng trả lời hai câu hỏi để truy cập Funnel Tracking.</p>
        <form onSubmit={submit}>
          <fieldset disabled={pending} className={styles.questions}>
            <fieldset className={styles.choices}>
              <legend>Shri Mataji là ai?</legend>
              {["Shri Adi Shakti", "Shri Buddha", "Shi Jesus"].map((answer) => (
                <label className={styles.choice} key={answer}>
                  <input type="radio" name="identity" value={answer} required />
                  <span>{answer}</span>
                </label>
              ))}
            </fieldset>
            <label htmlFor="technique" className={styles.question}>Kỹ thuật nào để hỗ trợ cho một luân xa, hoặc khi cần trợ giúp một ai đó, bắt đầu bởi ký tự “b”?</label>
            <input id="technique" name="technique" className={styles.answer} type="text" required maxLength={100} autoComplete="off" autoCapitalize="none" spellCheck={false} aria-describedby="answer-hint login-error" />
            <p id="answer-hint" className={styles.hint}>Không phân biệt chữ hoa, chữ thường.</p>
            <p id="login-error" role="alert" className={styles.error}>{error}</p>
            <button className={styles.submit} type="submit">{pending ? "Đang xác thực…" : "Tiếp tục"}</button>
          </fieldset>
        </form>
        <p className={styles.remember}>Phiên truy cập được ghi nhớ trên trình duyệt này trong 1 năm.</p>
      </section>
    </div>
  );
}
