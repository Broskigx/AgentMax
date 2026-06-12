export function MinimalBackground() {
  return (
    <div className="am-minimal-bg" aria-hidden="true">
      <div className="am-minimal-bg__layer am-minimal-bg__layer--base" />
      <div className="am-minimal-bg__layer am-minimal-bg__layer--accent" />
      <div className="am-minimal-bg__layer am-minimal-bg__layer--horizon" />
      <div className="am-minimal-bg__layer am-minimal-bg__layer--vignette" />
    </div>
  );
}
