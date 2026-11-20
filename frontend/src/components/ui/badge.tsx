import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";
import { cn } from "@/lib/utils";

const badgeVariants = cva("inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium", {
  variants: {
    variant: {
      default: "border-transparent bg-slate-900 text-white",
      secondary: "border-transparent bg-slate-100 text-slate-900",
      success: "border-transparent bg-green-100 text-green-900",
      warning: "border-transparent bg-amber-100 text-amber-900",
      danger: "border-transparent bg-red-100 text-red-900",
      outline: "text-slate-900 border-slate-300",
    },
  },
  defaultVariants: { variant: "default" },
});

export function Badge({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

const STATE_VARIANT: Record<string, VariantProps<typeof badgeVariants>["variant"]> = {
  created: "secondary", notified: "secondary", in_progress: "warning", submitted: "default", closed: "success",
  overdue: "danger", escalated: "danger", draft: "secondary", validated: "default", approved: "success",
  active: "success", expired: "warning", revoked: "danger", private: "secondary", shared: "default",
  withdrawn: "warning", deleted: "danger", pending: "warning", executed: "success", rejected: "danger",
  failed: "danger", proposed: "warning", confirmed: "success", applied: "secondary",
};

export function StateBadge({ state = "" }: { state?: string }) {
  return <Badge variant={STATE_VARIANT[state] ?? "outline"}>{state.replace("_", " ")}</Badge>;
}
