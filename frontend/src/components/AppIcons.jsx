import React from 'react';

function IconBase({ size = 18, strokeWidth = 1.9, children, style, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={style}
      aria-hidden="true"
      {...props}
    >
      {children}
    </svg>
  );
}

export function PlusIcon(props) {
  return <IconBase {...props}><path d="M12 5v14" /><path d="M5 12h14" /></IconBase>;
}

export function MenuIcon(props) {
  return <IconBase {...props}><path d="M4 7h16" /><path d="M4 12h16" /><path d="M4 17h16" /></IconBase>;
}

export function ChevronLeftIcon(props) {
  return <IconBase {...props}><path d="m15 18-6-6 6-6" /></IconBase>;
}

export function ChevronDownIcon(props) {
  return <IconBase {...props}><path d="m6 9 6 6 6-6" /></IconBase>;
}

export function ConversationIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M7 18H4a1 1 0 0 1-1-1V6a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H9l-4 3v-3Z" />
    </IconBase>
  );
}

export function StoryIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M6 4h9a3 3 0 0 1 3 3v11a2 2 0 0 1-2 2H8a3 3 0 0 1-3-3V5a1 1 0 0 1 1-1Z" />
      <path d="M9 8h6" />
      <path d="M9 12h6" />
      <path d="M9 16h4" />
    </IconBase>
  );
}

export function FastIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M13 2 5 14h6l-1 8 8-12h-6l1-8Z" />
    </IconBase>
  );
}

export function ThinkingIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M9 18h6" />
      <path d="M10 22h4" />
      <path d="M12 2a7 7 0 0 0-4 12.7c.6.4 1 1 1 1.7V17h6v-.6c0-.7.4-1.3 1-1.7A7 7 0 0 0 12 2Z" />
    </IconBase>
  );
}

export function ProIcon(props) {
  return (
    <IconBase {...props}>
      <path d="m12 3 2.9 5.9 6.5.9-4.7 4.6 1.1 6.5L12 18l-5.8 3.1 1.1-6.5-4.7-4.6 6.5-.9L12 3Z" />
    </IconBase>
  );
}

export function ExportIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M12 3v12" />
      <path d="m7 10 5 5 5-5" />
      <path d="M5 21h14" />
    </IconBase>
  );
}

export function BookIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M5 4.5A2.5 2.5 0 0 1 7.5 2H20v16H7.5A2.5 2.5 0 0 0 5 20.5" />
      <path d="M5 4.5v16" />
      <path d="M9 6h7" />
      <path d="M9 10h7" />
    </IconBase>
  );
}

export function EditIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M12 20h9" />
      <path d="m16.5 3.5 4 4L7 21l-4 1 1-4Z" />
    </IconBase>
  );
}

export function TrashIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M3 6h18" />
      <path d="M8 6V4h8v2" />
      <path d="m19 6-1 14H6L5 6" />
      <path d="M10 11v6" />
      <path d="M14 11v6" />
    </IconBase>
  );
}

export function AttachmentIcon(props) {
  return (
    <IconBase {...props}>
      <path d="m21.4 11.1-8.5 8.5a5 5 0 0 1-7.1-7.1l9.2-9.2a3.5 3.5 0 0 1 5 5l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" />
    </IconBase>
  );
}

export function ImageIcon(props) {
  return (
    <IconBase {...props}>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <circle cx="9" cy="10" r="1.5" />
      <path d="m21 16-4.5-4.5L7 21" />
    </IconBase>
  );
}

export function SendIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M22 2 11 13" />
      <path d="m22 2-7 20-4-9-9-4Z" />
    </IconBase>
  );
}

export function MicrophoneIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M12 15a3 3 0 0 0 3-3V7a3 3 0 1 0-6 0v5a3 3 0 0 0 3 3Z" />
      <path d="M19 11a7 7 0 0 1-14 0" />
      <path d="M12 18v4" />
      <path d="M8 22h8" />
    </IconBase>
  );
}

export function StopIcon(props) {
  return (
    <IconBase {...props}>
      <rect x="6" y="6" width="12" height="12" rx="2.5" />
    </IconBase>
  );
}

export function RefreshIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M3 12a9 9 0 0 1 15.3-6.3L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15.3 6.3L3 16" />
      <path d="M8 16H3v5" />
    </IconBase>
  );
}

export function WarningIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
      <path d="m10.3 3.9-8 14a1.2 1.2 0 0 0 1 1.8h17.4a1.2 1.2 0 0 0 1-1.8l-8-14a1.2 1.2 0 0 0-2 0Z" />
    </IconBase>
  );
}

export function LockIcon(props) {
  return (
    <IconBase {...props}>
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V8a4 4 0 1 1 8 0v3" />
    </IconBase>
  );
}

export function UserAddIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M15 19a6 6 0 0 0-12 0" />
      <circle cx="9" cy="7" r="4" />
      <path d="M19 8v6" />
      <path d="M16 11h6" />
    </IconBase>
  );
}

export function LogoutIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="m16 17 5-5-5-5" />
      <path d="M21 12H9" />
    </IconBase>
  );
}

export function SearchIcon(props) {
  return (
    <IconBase {...props}>
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3" />
    </IconBase>
  );
}

export function GlobeIcon(props) {
  return (
    <IconBase {...props}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18" />
      <path d="M12 3a15 15 0 0 1 0 18" />
      <path d="M12 3a15 15 0 0 0 0 18" />
    </IconBase>
  );
}

export function LinkIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M10 13a5 5 0 0 0 7 0l2-2a5 5 0 0 0-7-7l-1 1" />
      <path d="M14 11a5 5 0 0 0-7 0l-2 2a5 5 0 0 0 7 7l1-1" />
    </IconBase>
  );
}

export function AnalyticsIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M4 20V10" />
      <path d="M10 20V4" />
      <path d="M16 20v-7" />
      <path d="M22 20v-11" />
    </IconBase>
  );
}

export function ChartIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M3 3v18h18" />
      <path d="m7 14 4-4 3 3 5-6" />
    </IconBase>
  );
}

export function FileIcon(props) {
  return (
    <IconBase {...props}>
      <path d="M14 2H7a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7Z" />
      <path d="M14 2v5h5" />
    </IconBase>
  );
}
