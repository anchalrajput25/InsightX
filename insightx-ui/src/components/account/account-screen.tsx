"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { toast } from "sonner";
import { KeyRound, Loader2, LogOut, Monitor, Moon, ShieldOff, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { PageHeader } from "@/components/common/page-header";
import { ErrorState } from "@/components/common/states";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/components/providers/auth-provider";
import { authApi } from "@/lib/api/auth";
import { errorMessage } from "@/lib/api/client";
import { formatDay, initials, timeAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

const passwordSchema = z
  .object({
    current: z.string().min(1, "Enter your current password."),
    next: z
      .string()
      .min(8, "Use at least 8 characters.")
      .refine((value) => !/^\d+$/.test(value) && !/^[a-zA-Z]+$/.test(value), "Mix letters with numbers or symbols."),
    confirm: z.string(),
  })
  .refine((values) => values.next === values.confirm, { path: ["confirm"], message: "The two new passwords do not match." })
  .refine((values) => values.next !== values.current, { path: ["next"], message: "Choose a password different from the current one." });
type PasswordValues = z.infer<typeof passwordSchema>;

function PasswordCard() {
  const form = useForm<PasswordValues>({ resolver: zodResolver(passwordSchema), defaultValues: { current: "", next: "", confirm: "" } });
  const { errors, isSubmitting } = form.formState;
  const [serverError, setServerError] = React.useState("");
  const submit = form.handleSubmit(async (values) => {
    setServerError("");
    try {
      await authApi.changePassword(values.current, values.next);
      form.reset();
      toast.success("Password changed");
    } catch (error) {
      setServerError(errorMessage(error));
    }
  });
  const field = (name: keyof PasswordValues, label: string, autoComplete: string) => (
    <div className="space-y-1.5">
      <Label htmlFor={`pw-${name}`}>{label}</Label>
      <Input
        id={`pw-${name}`}
        type="password"
        autoComplete={autoComplete}
        aria-invalid={!!errors[name]}
        aria-describedby={errors[name] ? `pw-${name}-error` : undefined}
        {...form.register(name)}
      />
      {errors[name] && (
        <p id={`pw-${name}-error`} role="alert" className="text-xs text-critical">
          {errors[name]?.message}
        </p>
      )}
    </div>
  );
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="size-4 text-muted-foreground" /> Change password
        </CardTitle>
        <CardDescription>At least 8 characters, with letters and numbers or symbols.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} noValidate className="space-y-4">
          {field("current", "Current password", "current-password")}
          <div className="grid gap-4 sm:grid-cols-2">
            {field("next", "New password", "new-password")}
            {field("confirm", "New password again", "new-password")}
          </div>
          {serverError && (
            <p role="alert" className="rounded-md border border-critical/30 bg-critical/10 px-3 py-2 text-[13px] text-critical">
              {serverError}
            </p>
          )}
          <div className="flex justify-end">
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting && <Loader2 className="animate-spin" />} Change password
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function ThemeCard() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  const options = [
    { value: "dark", label: "Dark", icon: Moon },
    { value: "light", label: "Light", icon: Sun },
    { value: "system", label: "System", icon: Monitor },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Appearance</CardTitle>
        <CardDescription>Saved in this browser.</CardDescription>
      </CardHeader>
      <CardContent>
        <div role="radiogroup" aria-label="Theme" className="grid grid-cols-3 gap-2">
          {options.map((option) => {
            const active = mounted && theme === option.value;
            return (
              <button
                key={option.value}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setTheme(option.value)}
                className={cn(
                  "flex flex-col items-center gap-2 rounded-lg border px-3 py-4 text-[13px] transition-colors hover:border-foreground/20",
                  active && "border-primary/60 bg-primary/[0.06]",
                )}
              >
                <option.icon className={cn("size-4", active ? "text-primary" : "text-muted-foreground")} />
                {option.label}
              </button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

export function AccountScreen() {
  const { state, signOut } = useAuth();
  const required = state.phase === "ready" && state.required;
  const me = useQuery({ queryKey: ["me"], queryFn: () => authApi.me(), enabled: required });
  const [leaving, setLeaving] = React.useState(false);

  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 px-4 py-8 sm:px-8">
      <PageHeader title="Account" description="Your profile, password and preferences." />

      {!required ? (
        <Card>
          <CardContent className="flex items-start gap-3 pt-6">
            <ShieldOff className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
            <div>
              <p className="text-sm font-medium">Sign-in is turned off on this server</p>
              <p className="mt-1 text-sm text-muted-foreground">Everyone using this backend shares one workspace. Turn sign-in on in the backend settings to give each person their own chats, history and alerts.</p>
            </div>
          </CardContent>
        </Card>
      ) : me.isPending ? (
        <Skeleton className="h-36 rounded-xl" />
      ) : me.isError ? (
        <ErrorState error={me.error} onRetry={() => void me.refetch()} />
      ) : (
        <Card>
          <CardContent className="flex flex-col gap-5 pt-6 sm:flex-row sm:items-center">
            <Avatar className="size-14">
              <AvatarFallback className="text-lg font-semibold">{initials(me.data.user.name || me.data.user.email)}</AvatarFallback>
            </Avatar>
            <div className="min-w-0 flex-1">
              <p className="truncate text-lg font-semibold">{me.data.user.name}</p>
              <p className="truncate text-sm text-muted-foreground">{me.data.user.email}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                Joined {formatDay(me.data.user.created_at)}
                {me.data.user.last_login_at ? ` · last sign-in ${timeAgo(me.data.user.last_login_at)}` : ""}
              </p>
            </div>
            <div className="w-full sm:w-48">
              <div className="flex justify-between text-xs">
                <span className="text-muted-foreground">Questions today</span>
                <span className="tabular font-medium">
                  {me.data.questions_today}
                  {me.data.daily_limit ? ` / ${me.data.daily_limit}` : ""}
                </span>
              </div>
              {me.data.daily_limit ? (
                <div className="mt-1.5 h-1.5 rounded-full bg-muted">
                  <div
                    className={cn("h-1.5 rounded-full", me.data.questions_today / me.data.daily_limit > 0.9 ? "bg-critical" : "bg-primary")}
                    style={{ width: `${Math.min(100, (me.data.questions_today / me.data.daily_limit) * 100)}%` }}
                  />
                </div>
              ) : (
                <p className="mt-1 text-[11px] text-muted-foreground">No daily limit</p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {required && <PasswordCard />}
      <ThemeCard />

      {required && (
        <Card className="border-critical/20">
          <CardHeader>
            <CardTitle className="text-base">Sessions</CardTitle>
            <CardDescription>Sign out here, or on every device where you are signed in.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={signOut}>
              <LogOut /> Sign out
            </Button>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="outline" className="text-critical hover:text-critical" disabled={leaving}>
                  {leaving ? <Loader2 className="animate-spin" /> : <LogOut />} Sign out everywhere
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Sign out on every device?</AlertDialogTitle>
                  <AlertDialogDescription>All your sessions end, including this one and the Streamlit app. You will need to sign in again.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction
                    destructive
                    onClick={async () => {
                      setLeaving(true);
                      try {
                        await authApi.logoutEverywhere();
                        toast.success("Signed out on every device");
                        signOut();
                      } catch (error) {
                        toast.error("Could not sign out everywhere", { description: errorMessage(error) });
                      } finally {
                        setLeaving(false);
                      }
                    }}
                  >
                    Sign out everywhere
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
