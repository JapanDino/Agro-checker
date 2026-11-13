import type * as React from 'react';

export type FieldStatus = 'risk' | 'check' | 'normal' | 'none';
export type IconName = 'map' | 'today' | 'trips' | 'analysis' | 'task' | 'history' | 'check' | 'spot' | 'info' | 'alert' | 'arrow';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'soft' | 'text';
  size?: 'sm' | 'md' | 'lg';
  wide?: boolean;
  icon?: IconName;
}
export declare function Button(props: ButtonProps): React.ReactElement;

export interface StatusChipProps { status?: FieldStatus; children?: React.ReactNode; className?: string }
export declare function StatusChip(props: StatusChipProps): React.ReactElement;

export interface TagProps { tone?: 'green' | 'neutral'; children?: React.ReactNode; className?: string }
export declare function Tag(props: TagProps): React.ReactElement;

export interface SummaryTileProps { label: React.ReactNode; value: React.ReactNode; hint?: React.ReactNode; status?: Exclude<FieldStatus, 'none'>; className?: string }
export declare function SummaryTile(props: SummaryTileProps): React.ReactElement;

export interface FieldRowProps {
  id: string;
  status?: FieldStatus;
  crop: React.ReactNode;
  detail?: React.ReactNode;
  due?: React.ReactNode;
  dueLabel?: React.ReactNode;
  reasons?: React.ReactNode[];
  source?: React.ReactNode;
  sensor?: boolean;
  mark?: 'clean' | 'focus' | 'none';
  actions?: React.ReactNode;
  children?: React.ReactNode;
  className?: string;
}
export declare function FieldRow(props: FieldRowProps): React.ReactElement;

export interface IndexStatProps { label: React.ReactNode; value: React.ReactNode; change?: React.ReactNode; trend?: 'up' | 'down'; className?: string }
export declare function IndexStat(props: IndexStatProps): React.ReactElement;

export interface SegmentedOption { value: string; label: React.ReactNode }
export interface SegmentedProps { options: SegmentedOption[]; value?: string; defaultValue?: string; onChange?: (value: string) => void; label?: string; wide?: boolean; className?: string }
export declare function Segmented(props: SegmentedProps): React.ReactElement;

export interface TextFieldProps {
  label: React.ReactNode;
  id?: string; name?: string; type?: string; placeholder?: string;
  value?: string; defaultValue?: string; onChange?: React.ChangeEventHandler<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>;
  options?: { value: string; label: React.ReactNode }[];
  multiline?: boolean; required?: boolean;
  hint?: React.ReactNode; error?: React.ReactNode;
  width?: number | string; className?: string;
}
export declare function TextField(props: TextFieldProps): React.ReactElement;

export interface InspectionMarkProps { value?: 'clean' | 'focus' | null; defaultValue?: 'clean' | 'focus'; onChange?: (value: 'clean' | 'focus') => void; cleanHint?: React.ReactNode; focusHint?: React.ReactNode; label?: string; className?: string }
export declare function InspectionMark(props: InspectionMarkProps): React.ReactElement;

export interface NoticeProps { tone?: 'success' | 'example' | 'error' | 'neutral'; title?: React.ReactNode; children?: React.ReactNode; className?: string }
export declare function Notice(props: NoticeProps): React.ReactElement;

export interface NavItem { id: string; label: React.ReactNode; icon?: IconName; badge?: number }
export interface NavRailProps { items?: NavItem[]; active?: string; defaultActive?: string; onChange?: (id: string) => void; horizontal?: boolean; className?: string }
export declare function NavRail(props: NavRailProps): React.ReactElement;

export interface TopBarProps { farm?: React.ReactNode; farmDetail?: React.ReactNode; children?: React.ReactNode; className?: string }
export declare function TopBar(props: TopBarProps): React.ReactElement;

export interface TodayScreenProps {}
export declare function TodayScreen(props: TodayScreenProps): React.ReactElement;

declare global {
  interface Window {
    AgroPulse: {
      Button: typeof Button; StatusChip: typeof StatusChip; Tag: typeof Tag; SummaryTile: typeof SummaryTile;
      FieldRow: typeof FieldRow; IndexStat: typeof IndexStat; Segmented: typeof Segmented; TextField: typeof TextField;
      InspectionMark: typeof InspectionMark; Notice: typeof Notice; NavRail: typeof NavRail; TopBar: typeof TopBar;
      TodayScreen: typeof TodayScreen; Icon: (props: { name: IconName }) => React.ReactElement;
    };
  }
}
