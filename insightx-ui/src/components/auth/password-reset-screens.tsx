"use client";

// "Forgot password?" and "Choose a new password" screens, in the same dark
// style as sign-in (without the 3D scene). The backend always gives the
// same answer to a reset request, so these screens never say whether an
// email has an account.

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { ArrowLeft, ArrowRight, CheckCircle2, Eye, EyeOff, KeyRound, Loader2, MailCheck } from "lucide-react";
import { LogoMark } from "@/components/common/logo";
import { GridPattern } from "@/components/fx/grid-pattern";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { passwordResetApi } from "@/lib/api/password-reset";
import { errorMessage } from "@/lib/api/client";

const forgotSchema = z.object({
  email: z.string().trim().min(1, "Enter your email.").email("Enter a valid email."),
});

// Same rules as sign-up (and the backend).
const resetSchema = z
  .object({
    password: z
      .string()
      .min(8, "Use at least 8 characters.")
      .refine((value) => !/^\d+$/.test(value) && !/^[a-zA-Z]+$/.test(value), "Mix letters with numbers or symbols."),
    confirm: z.string().min(1, "Type the new password again."),
  })
  .refine((values) => values.password === values.confirm, { path: ["confirm"], message: "The two passwords do not match." });

type ForgotValues = z.infer<typeof forgotSchema>;
type ResetValues = z.infer<typeof resetSchema>;

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="dark relative isolate flex min-h-dvh items-center justify-center overflow-hidden bg-[#06070a] p-6 text-foreground">
      <div aria-hidden className="absolute inset-0 -z-10 bg-[radial-gradient(ellipse_60%_50%_at_50%_40%,rgb(124_58_237/0.16),transparent_70%)]" />
      <GridPattern variant="dots" />
      <div className="fx-enter fx-beam fx-surface w-full max-w-sm rounded-2xl border bg-card/80 p-6 shadow-[0_24px_80px_-24px_rgb(0_0_0/0.5)] backdrop-blur-sm sm:p-8">
        <Link href="/" className="mb-8 flex items-center gap-2.5" aria-label="InsightX home">
          <LogoMark />
          <span className="text-[15px] font-semibold tracking-tight">InsightX</span>
        </Link>
        {children}
      </div>
    </div>
  );
}

function Problem({ id, text }: { id: string; text?: string }) {
  if (!text) return null;
  return (
    <p id={id} role="alert" className="text-xs text-critical">
      {text}
    </p>
  );
}

function ServerProblem({ text }: { text: string }) {
  if (!text) return null;
  return (
    <p role="alert" className="rounded-md border border-critical/30 bg-critical/10 px-3 py-2 text-[13px] text-critical">
      {text}
    </p>
  );
}

function BackToSignIn() {
  return (
    <Link href="/sign-in" className="mt-6 inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
      <ArrowLeft className="size-4" />
      Back to sign in
    </Link>
  );
}

/* ------------------------------------------------------------ forgot */

export function ForgotPasswordScreen() {
  const [sent, setSent] = React.useState("");
  const [serverError, setServerError] = React.useState("");
  const form = useForm<ForgotValues>({ resolver: zodResolver(forgotSchema), defaultValues: { email: "" } });
  const { errors, isSubmitting } = form.formState;

  const submit = form.handleSubmit(async (values) => {
    setServerError("");
    try {
      const result = await passwordResetApi.forgot(values.email.trim());
      setSent(result.message);
    } catch (error) {
      setServerError(errorMessage(error));
    }
  });

  if (sent) {
    return (
      <Shell>
        <MailCheck className="size-8 text-primary" />
        <h1 className="fx-title mt-4 text-2xl font-semibold tracking-tight">Check your email</h1>
        <p className="mt-2 text-sm text-muted-foreground">{sent}</p>
        <p className="mt-3 text-sm text-muted-foreground">Not there after a few minutes? Look in spam, or try again.</p>
        <BackToSignIn />
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="fx-title text-2xl font-semibold tracking-tight">Forgot your password?</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">Enter your account email and we will send you a link to choose a new one.</p>
      <form onSubmit={submit} noValidate className="mt-6 space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="reset-email">Email</Label>
          <Input
            id="reset-email"
            type="email"
            autoComplete="email"
            placeholder="you@company.com"
            aria-invalid={!!errors.email}
            aria-describedby={errors.email ? "reset-email-error" : undefined}
            {...form.register("email")}
          />
          <Problem id="reset-email-error" text={errors.email?.message} />
        </div>
        <ServerProblem text={serverError} />
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? <Loader2 className="animate-spin" /> : <ArrowRight />}
          Send reset link
        </Button>
      </form>
      <BackToSignIn />
    </Shell>
  );
}

/* ------------------------------------------------------------ reset */

function NewPasswordInput({ id, invalid, describedBy, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { id: string; invalid: boolean; describedBy?: string }) {
  const [visible, setVisible] = React.useState(false);
  return (
    <div className="relative">
      <Input id={id} type={visible ? "text" : "password"} autoComplete="new-password" aria-invalid={invalid} aria-describedby={describedBy} className="pr-10" {...props} />
      <button
        type="button"
        onClick={() => setVisible((value) => !value)}
        aria-label={visible ? "Hide password" : "Show password"}
        className="absolute right-1.5 top-1/2 flex size-7 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:text-foreground"
      >
        {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  );
}

export function ResetPasswordScreen() {
  const params = useSearchParams();
  // Read the token once, then take it out of the address bar.
  const [token] = React.useState(() => params.get("token") ?? "");
  const [done, setDone] = React.useState(false);
  const [serverError, setServerError] = React.useState("");
  const form = useForm<ResetValues>({ resolver: zodResolver(resetSchema), defaultValues: { password: "", confirm: "" } });
  const { errors, isSubmitting } = form.formState;

  React.useEffect(() => {
    if (token && window.location.search) window.history.replaceState(null, "", window.location.pathname);
  }, [token]);

  const submit = form.handleSubmit(async (values) => {
    setServerError("");
    try {
      await passwordResetApi.reset(token, values.password);
      setDone(true);
    } catch (error) {
      setServerError(errorMessage(error));
    }
  });

  if (!token) {
    return (
      <Shell>
        <KeyRound className="size-8 text-primary" />
        <h1 className="fx-title mt-4 text-2xl font-semibold tracking-tight">This link is incomplete</h1>
        <p className="mt-2 text-sm text-muted-foreground">Open the link from the email again, or ask for a new one.</p>
        <Button asChild className="mt-6 w-full">
          <Link href="/forgot-password">Ask for a new link</Link>
        </Button>
        <BackToSignIn />
      </Shell>
    );
  }

  if (done) {
    return (
      <Shell>
        <CheckCircle2 className="size-8 text-primary" />
        <h1 className="fx-title mt-4 text-2xl font-semibold tracking-tight">Password changed</h1>
        <p className="mt-2 text-sm text-muted-foreground">You were signed out on every device. Sign in with your new password.</p>
        <Button asChild className="mt-6 w-full">
          <Link href="/sign-in">
            <ArrowRight />
            Sign in
          </Link>
        </Button>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="fx-title text-2xl font-semibold tracking-tight">Choose a new password</h1>
      <p className="mt-1.5 text-sm text-muted-foreground">At least 8 characters, with letters and numbers or symbols.</p>
      <form onSubmit={submit} noValidate className="mt-6 space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="new-password">New password</Label>
          <NewPasswordInput id="new-password" invalid={!!errors.password} describedBy={errors.password ? "new-password-error" : undefined} {...form.register("password")} />
          <Problem id="new-password-error" text={errors.password?.message} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="confirm-password">New password again</Label>
          <NewPasswordInput id="confirm-password" invalid={!!errors.confirm} describedBy={errors.confirm ? "confirm-password-error" : undefined} {...form.register("confirm")} />
          <Problem id="confirm-password-error" text={errors.confirm?.message} />
        </div>
        <ServerProblem text={serverError} />
        {serverError && /not valid|expired/i.test(serverError) ? (
          <Link href="/forgot-password" className="block text-sm text-primary hover:underline">
            Ask for a new link
          </Link>
        ) : null}
        <Button type="submit" className="w-full" disabled={isSubmitting}>
          {isSubmitting ? <Loader2 className="animate-spin" /> : <KeyRound />}
          Save new password
        </Button>
      </form>
      <BackToSignIn />
    </Shell>
  );
}
