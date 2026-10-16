"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { ArrowRight, BrainCircuit, Eye, EyeOff, Loader2, SearchCheck, ShieldCheck } from "lucide-react";
import { LogoMark } from "@/components/common/logo";
import { AuroraBackground } from "@/components/fx/aurora-background";
import { GridPattern } from "@/components/fx/grid-pattern";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/components/providers/auth-provider";
import { authApi } from "@/lib/api/auth";
import { errorMessage } from "@/lib/api/client";

const signInSchema = z.object({
  email: z.string().trim().min(1, "Enter your email.").email("Enter a valid email."),
  password: z.string().min(1, "Enter your password."),
});

// Mirrors the backend rules so people see problems before sending.
const signUpSchema = z.object({
  name: z.string().trim().min(1, "Enter your name.").max(80, "Use 80 characters or fewer."),
  email: z.string().trim().min(1, "Enter your email.").email("Enter a valid email."),
  password: z
    .string()
    .min(8, "Use at least 8 characters.")
    .refine((value) => !/^\d+$/.test(value) && !/^[a-zA-Z]+$/.test(value), "Mix letters with numbers or symbols."),
});

type SignInValues = z.infer<typeof signInSchema>;
type SignUpValues = z.infer<typeof signUpSchema>;

function Field({ id, label, error, children }: { id: string; label: string; error?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error && (
        <p id={`${id}-error`} role="alert" className="text-xs text-critical">
          {error}
        </p>
      )}
    </div>
  );
}

function PasswordInput({ id, invalid, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { id: string; invalid: boolean }) {
  const [visible, setVisible] = React.useState(false);
  return (
    <div className="relative">
      <Input id={id} type={visible ? "text" : "password"} aria-invalid={invalid} aria-describedby={invalid ? `${id}-error` : undefined} className="pr-10" {...props} />
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

function SignInForm({ onDone }: { onDone: () => void }) {
  const { signedIn } = useAuth();
  const [serverError, setServerError] = React.useState("");
  const form = useForm<SignInValues>({ resolver: zodResolver(signInSchema), defaultValues: { email: "", password: "" } });
  const { errors, isSubmitting } = form.formState;

  const submit = form.handleSubmit(async (values) => {
    setServerError("");
    try {
      signedIn(await authApi.login(values.email.trim(), values.password));
      onDone();
    } catch (error) {
      setServerError(errorMessage(error));
    }
  });

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <Field id="email" label="Email" error={errors.email?.message}>
        <Input id="email" type="email" autoComplete="email" placeholder="you@company.com" aria-invalid={!!errors.email} aria-describedby={errors.email ? "email-error" : undefined} {...form.register("email")} />
      </Field>
      <Field id="password" label="Password" error={errors.password?.message}>
        <PasswordInput id="password" autoComplete="current-password" invalid={!!errors.password} {...form.register("password")} />
      </Field>
      {serverError && (
        <p role="alert" className="rounded-md border border-critical/30 bg-critical/10 px-3 py-2 text-[13px] text-critical">
          {serverError}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? <Loader2 className="animate-spin" /> : <ArrowRight />}
        Sign in
      </Button>
    </form>
  );
}

function SignUpForm({ onDone }: { onDone: () => void }) {
  const { signedIn } = useAuth();
  const [serverError, setServerError] = React.useState("");
  const form = useForm<SignUpValues>({ resolver: zodResolver(signUpSchema), defaultValues: { name: "", email: "", password: "" } });
  const { errors, isSubmitting } = form.formState;

  const submit = form.handleSubmit(async (values) => {
    setServerError("");
    try {
      signedIn(await authApi.signup(values.email.trim(), values.name.trim(), values.password));
      onDone();
    } catch (error) {
      setServerError(errorMessage(error));
    }
  });

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <Field id="name" label="Name" error={errors.name?.message}>
        <Input id="name" autoComplete="name" placeholder="Your name" aria-invalid={!!errors.name} aria-describedby={errors.name ? "name-error" : undefined} {...form.register("name")} />
      </Field>
      <Field id="signup-email" label="Email" error={errors.email?.message}>
        <Input id="signup-email" type="email" autoComplete="email" placeholder="you@company.com" aria-invalid={!!errors.email} aria-describedby={errors.email ? "signup-email-error" : undefined} {...form.register("email")} />
      </Field>
      <Field id="signup-password" label="Password" error={errors.password?.message}>
        <PasswordInput id="signup-password" autoComplete="new-password" invalid={!!errors.password} {...form.register("password")} />
      </Field>
      {!errors.password && <p className="-mt-2 text-xs text-muted-foreground">At least 8 characters, with letters and numbers or symbols.</p>}
      {serverError && (
        <p role="alert" className="rounded-md border border-critical/30 bg-critical/10 px-3 py-2 text-[13px] text-critical">
          {serverError}
        </p>
      )}
      <Button type="submit" className="w-full" disabled={isSubmitting}>
        {isSubmitting ? <Loader2 className="animate-spin" /> : <ArrowRight />}
        Create account
      </Button>
    </form>
  );
}

const POINTS = [
  { icon: BrainCircuit, title: "Ask in plain English", text: "Questions become checked SQL with a trust score." },
  { icon: SearchCheck, title: "Find the real reason", text: "Investigations rank the drivers behind every change." },
  { icon: ShieldCheck, title: "Read-only and traced", text: "Every step, AI call and query is recorded." },
];

export function SignInScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const { state } = useAuth();
  const [mode, setMode] = React.useState<"in" | "up">("in");
  const next = params.get("next");
  const target = next && next.startsWith("/") && !next.startsWith("//") ? next : "/ask";

  React.useEffect(() => {
    if (state.phase === "ready") router.replace(target);
  }, [state.phase, router, target]);

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <section className="relative isolate hidden overflow-hidden border-r bg-sidebar lg:flex lg:flex-col lg:justify-between lg:p-12">
        <AuroraBackground className="[mask-image:none]" />
        <GridPattern className="[mask-image:radial-gradient(ellipse_at_30%_40%,black,transparent_70%)]" />
        <div className="relative flex items-center gap-2.5">
          <LogoMark />
          <span className="text-[15px] font-semibold tracking-tight">InsightX</span>
        </div>
        <div className="relative max-w-md">
          <h2 className="fx-enter text-4xl font-semibold leading-[1.1] tracking-tight text-balance">
            <span className="fx-title">Answers from your data, with </span>
            <span className="fx-gradient-text">the reasons behind them.</span>
          </h2>
          <ul className="fx-stagger mt-8 space-y-5">
            {POINTS.map((point) => (
              <li key={point.title} className="flex gap-3.5">
                <span className="fx-surface flex size-9 shrink-0 items-center justify-center rounded-lg border bg-card/70 backdrop-blur">
                  <point.icon className="size-4 text-primary" />
                </span>
                <span>
                  <span className="block text-sm font-medium">{point.title}</span>
                  <span className="block text-sm text-muted-foreground">{point.text}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-muted-foreground">Your database password stays on your server. This app never stores it.</p>
      </section>

      <section className="relative isolate flex items-center justify-center overflow-hidden p-6">
        <GridPattern variant="dots" className="lg:hidden" />
        <div className="fx-enter fx-beam fx-surface w-full max-w-sm rounded-2xl border bg-card/80 p-6 shadow-[0_24px_80px_-24px_rgb(0_0_0/0.5)] backdrop-blur-sm sm:p-8">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <LogoMark />
            <span className="text-[15px] font-semibold tracking-tight">InsightX</span>
          </div>
          <h1 className="fx-title text-2xl font-semibold tracking-tight">{mode === "in" ? "Welcome back" : "Create your account"}</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">{mode === "in" ? "Sign in to continue to your workspace." : "It takes less than a minute."}</p>

          {state.phase === "offline" ? (
            <p role="alert" className="mt-6 rounded-md border border-critical/30 bg-critical/10 px-3 py-2 text-[13px] text-critical">
              {state.message}
            </p>
          ) : null}

          <Tabs value={mode} onValueChange={(value) => setMode(value as "in" | "up")} className="mt-6">
            <TabsList className="mb-6 w-full">
              <TabsTrigger value="in" className="flex-1">
                Sign in
              </TabsTrigger>
              <TabsTrigger value="up" className="flex-1">
                Sign up
              </TabsTrigger>
            </TabsList>
          </Tabs>
          {mode === "in" ? <SignInForm onDone={() => router.replace(target)} /> : <SignUpForm onDone={() => router.replace(target)} />}
        </div>
      </section>
    </div>
  );
}
