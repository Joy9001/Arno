import { Bell, BellOff, BellRing } from "lucide-react";

export function PushStatus({ permission, subscribed }: {
  permission: NotificationPermission | "unsupported";
  subscribed: boolean;
}) {
  const label = permission === "granted" ? "Allowed" : permission === "denied"
    ? "Blocked in this browser" : permission === "unsupported" ? "Not supported" : "Not asked yet";
  return <>
    <p className="stg-status-line">
      <span className={`stg-status ${permission === "denied" ? "bad" : ""}`}>
        <Bell size={14} strokeWidth={1.8} aria-hidden="true" />{label}
      </span>
      <span className="stg-status">
        {subscribed ? <BellRing size={14} strokeWidth={1.8} aria-hidden="true" /> : <BellOff size={14} strokeWidth={1.8} aria-hidden="true" />}
        {subscribed ? "Subscribed" : "Not subscribed"}
      </span>
    </p>
    {permission === "denied" && <p className="stg-hint">Allow notifications for this site in your browser settings, then try again.</p>}
  </>;
}
