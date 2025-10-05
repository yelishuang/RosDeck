/* auth/login.js — 高对比浮动标签 + 限流 + 轻量埋点 */
(() => {
  "use strict";

  // ===== 配置 =====
  const API_LOGIN    = "/api/auth/login";
  const API_METRICS  = "/api/metrics";         // 埋点（没有也不报错）
  const DEFAULT_REDIRECT = "../index.html";
  const REQUEST_TIMEOUT_MS = 10000;

  const T = {
    loading: "正在登录…",
    success: "登录成功，即将跳转",
    invalid: "用户名或密码错误",
    required: "请完整填写用户名与密码",
    network: "网络异常或设备不可达",
    timeout: "请求超时，请稍后重试",
    locked: (sec) => `尝试过于频繁，请 ${sec}s 后再试`,
    unknown: "登录失败，请稍后重试",
    show: "显示密码", hide: "隐藏密码"
  };

  // ===== 工具 =====
  const $ = (s, r=document) => r.querySelector(s);
  const hasToastr = () => typeof window.toastr !== "undefined";
  const toast = {
    ok(m){ hasToastr()? (toastr.clear(), toastr.success(m)) : alert(m); },
    err(m){ hasToastr()? (toastr.clear(), toastr.error(m))  : alert(m); },
    info(m){ if (hasToastr()) { toastr.clear(); toastr.info(m); } }
  };
  const sleep = (ms)=>new Promise(r=>setTimeout(r,ms));

  // 非阻塞埋点
  async function metric(event, payload={}){
    try{
      const c=new AbortController(); setTimeout(()=>c.abort(),1500);
      await fetch(API_METRICS,{
        method:"POST", headers:{ "Content-Type":"application/json" },
        body: JSON.stringify({ event, ts: Date.now(), ua: navigator.userAgent, ...payload }),
        keepalive:true, signal:c.signal
      });
    }catch{}
  }

  const getCsrf = ()=>{
    const m=document.querySelector('meta[name="csrf-token"]');
    return m? m.getAttribute("content"): null;
  };

  async function fetchJSON(url, options={}, timeout=REQUEST_TIMEOUT_MS){
    const controller=new AbortController();
    const id=setTimeout(()=>controller.abort(), timeout);
    try{
      const res=await fetch(url,{...options, signal: controller.signal});
      const text=await res.text();
      let data={}; try{ data = text? JSON.parse(text): {}; }catch{}
      return { res, data };
    }finally{ clearTimeout(id); }
  }

  // ===== 业务逻辑 =====
  function mount(){
    const form = $("#loginForm"); if(!form) return;
    const user = $("#username");
    const pass = $("#password");
    const submit = form.querySelector('button[type="submit"]');
    const toggle = $("#togglePwd");

    metric("login_page_view");

    // 密码可见/隐藏
    toggle?.addEventListener("click", ()=>{
      const isPwd = pass.type === "password";
      pass.type = isPwd ? "text" : "password";
      if (toggle.firstElementChild?.classList){
        toggle.firstElementChild.classList.toggle("bi-eye", !isPwd);
        toggle.firstElementChild.classList.toggle("bi-eye-slash", isPwd);
      }
      toggle.setAttribute("aria-label", isPwd? T.hide : T.show);
      pass.focus();
    });

    // 状态切换
    function setLoading(loading){
      [user, pass, submit].forEach(el => el && (el.disabled = !!loading));
      if(submit){
        if(loading){ submit.dataset.originalText = submit.textContent; submit.textContent = T.loading; }
        else{ submit.textContent = submit.dataset.originalText || "登录"; }
      }
      form.classList.toggle("is-loading", !!loading);
    }

    function validate(u,p){ return (u||"").trim() && (p||"").trim(); }

    async function onSubmit(e){
      e.preventDefault();
      const username = user.value.trim();
      const password = pass.value.trim();

      if(!validate(username, password)){
        toast.err(T.required); user.focus(); return;
      }

      setLoading(true);

      try{
        const headers={ "Content-Type":"application/json" };
        const csrf=getCsrf(); if(csrf) headers["X-CSRF-Token"]=csrf;

        const { res, data } = await fetchJSON(API_LOGIN,{
          method:"POST",
          headers, credentials:"include",
          body: JSON.stringify({ username, password })
        });

        if(!res){ toast.err(T.network); metric("login_network_error"); return; }

        if(res.status===429){
          const retryAfter= +(res.headers.get("Retry-After") || data?.retryAfter || 15);
          toast.err(T.locked(retryAfter));
          metric("login_rate_limited",{ retryAfter });
          return;
        }

        if(res.status===401 || data?.code==="AUTH_INVALID" || data?.ok===false){
          toast.err(data?.message || T.invalid);
          metric("login_failed",{ reason: data?.code || res.status });
          pass.focus(); pass.select?.();
          return;
        }

        if(!res.ok){
          toast.err(T.unknown);
          metric("login_failed",{ reason: res.status });
          return;
        }

        toast.ok(T.success);
        metric("login_success");
        const target = data?.redirect || DEFAULT_REDIRECT;
        await sleep(250);
        window.location.assign(target);

      }catch(err){
        if(err?.name==="AbortError"){ toast.err(T.timeout); metric("login_timeout"); }
        else{ toast.err(navigator.onLine? T.unknown : T.network); metric("login_exception",{ message:String(err?.message||err) }); }
      }finally{
        setLoading(false);
      }
    }

    form.addEventListener("submit", onSubmit);
    pass.addEventListener("keydown", e=>{ if(e.key==="Enter") form.requestSubmit(); });
  }

  if(document.readyState==="loading"){
    document.addEventListener("DOMContentLoaded", mount, { once:true });
  }else{ mount(); }

})();
