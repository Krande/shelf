/**
 * What a space inherits, and who inherits it.
 *
 * Two halves, because inheritance has two owners. The space *doing* the
 * reading picks what it subscribes to. The space *being* read decides
 * whether it may be subscribed to at all, and can see and drop the
 * spaces that have.
 *
 * Both panels are owner-only, matching the API. Subscribing grants read
 * of the parent's items to everyone who can read this space, which is
 * not a decision an editor gets to make on the owner's behalf.
 */

import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Unlink } from "lucide-react";
import {
  fetchInherited,
  fetchSubscribable,
  fetchSubscribers,
  removeSubscriber,
  setSubscribable,
  subscribeToSpace,
  unsubscribeFromSpace,
  type Space,
  type Subscription,
} from "@/api/spaces";
import { ApiError } from "@/api/client";

export default function SpaceInheritance({ space }: { space: Space }) {
  return (
    <div
      className="mt-2 border-t pt-2"
      style={{ borderColor: "var(--color-border)" }}
    >
      <Inherits space={space} />
      {!space.is_personal && <OpenToOthers space={space} />}
    </div>
  );
}

/** The spaces this one reads from. */
function Inherits({ space }: { space: Space }) {
  const qc = useQueryClient();
  const [parent, setParent] = useState("");

  const inherited = useQuery<Subscription[], ApiError>({
    queryKey: ["inherits", space.slug],
    queryFn: () => fetchInherited(space.slug),
    retry: (_a, err) => err.status >= 500,
  });

  const available = useQuery<Subscription[], ApiError>({
    queryKey: ["subscribable"],
    queryFn: fetchSubscribable,
    retry: (_a, err) => err.status >= 500,
  });

  // Itself, and anything it already subscribes to, are not on offer.
  const taken = new Set([
    space.slug,
    ...(inherited.data ?? []).map((s) => s.slug),
  ]);
  const options = (available.data ?? []).filter((s) => !taken.has(s.slug));

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["inherits", space.slug] });
    // The library gains or loses items, and the pin list is filtered to
    // what the space can actually see.
    qc.invalidateQueries({ queryKey: ["items"] });
    qc.invalidateQueries({ queryKey: ["pins", space.slug] });
  };

  const add = useMutation({
    mutationFn: () => subscribeToSpace(space.slug, parent),
    onSuccess: () => {
      setParent("");
      invalidate();
    },
  });

  const drop = useMutation({
    mutationFn: (parentSlug: string) =>
      unsubscribeFromSpace(space.slug, parentSlug),
    onSuccess: invalidate,
  });

  function onAdd(e: FormEvent) {
    e.preventDefault();
    if (!parent || add.isPending) return;
    add.mutate();
  }

  const failure = add.error ?? drop.error;

  return (
    <div className="mb-3">
      <p className="mb-1 text-xs font-medium">Inherits from</p>

      {(inherited.data?.length ?? 0) > 0 ? (
        <ul className="mb-2 flex flex-col gap-1">
          {inherited.data?.map((s) => (
            <li key={s.slug} className="flex items-center gap-2 text-sm">
              <span className="min-w-0 flex-1">
                <span className="block truncate">{s.name}</span>
                <span
                  className="block truncate text-xs"
                  style={{ color: "var(--color-text-muted)" }}
                >
                  {s.slug} · read-only here
                </span>
              </span>
              <button
                onClick={() => drop.mutate(s.slug)}
                disabled={drop.isPending}
                aria-label={`Stop inheriting ${s.name}`}
                className="flex shrink-0 items-center gap-1 rounded border px-2 py-0.5 text-xs hover:opacity-80 disabled:opacity-50"
                style={{ borderColor: "var(--color-border)" }}
              >
                <Unlink className="h-3 w-3" />
                Remove
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-2 text-xs" style={{ color: "var(--color-text-muted)" }}>
          Nothing yet. Subscribe to a shared space to read its items here
          without holding a copy.
        </p>
      )}

      <form onSubmit={onAdd} className="flex flex-wrap gap-1">
        <select
          required
          value={parent}
          onChange={(e) => setParent(e.target.value)}
          aria-label={`Space for ${space.name} to inherit`}
          disabled={available.isLoading || options.length === 0}
          className="min-w-0 flex-1 rounded border px-2 py-1 text-xs disabled:opacity-50"
          style={{
            borderColor: "var(--color-border)",
            backgroundColor: "var(--color-surface)",
          }}
        >
          <option value="">
            {available.isLoading
              ? "Loading spaces…"
              : options.length === 0
                ? "No spaces are open to be inherited"
                : "Choose a space…"}
          </option>
          {options.map((s) => (
            <option key={s.slug} value={s.slug}>
              {s.name} ({s.slug})
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={add.isPending || !parent}
          className="flex shrink-0 items-center gap-1 rounded border px-2 py-1 text-xs hover:opacity-80 disabled:opacity-50"
          style={{ borderColor: "var(--color-border)" }}
        >
          {add.isPending && <Loader2 className="h-3 w-3 animate-spin" />}
          Inherit
        </button>
      </form>

      {failure && (
        <p className="mt-1 text-xs text-red-600" role="alert">
          {failure instanceof ApiError && failure.status === 403
            ? "That space isn't open for others to inherit."
            : failure.message}
        </p>
      )}
    </div>
  );
}

/** Whether other spaces may read this one, and which already do. */
function OpenToOthers({ space }: { space: Space }) {
  const qc = useQueryClient();

  const subscribers = useQuery<Subscription[], ApiError>({
    queryKey: ["subscribers", space.slug],
    queryFn: () => fetchSubscribers(space.slug),
    retry: (_a, err) => err.status >= 500,
  });

  // `subscribable` isn't on the Space the listing returns, so read it
  // off whichever subscription row mentions this space. The settings
  // mutation returns the fresh value and seeds this.
  const [open, setOpen] = useState<boolean | null>(null);
  const available = useQuery<Subscription[], ApiError>({
    queryKey: ["subscribable"],
    queryFn: fetchSubscribable,
    retry: (_a, err) => err.status >= 500,
  });
  const isOpen =
    open ?? (available.data ?? []).some((s) => s.slug === space.slug);

  const toggle = useMutation({
    mutationFn: (next: boolean) => setSubscribable(space.slug, next),
    onSuccess: (updated) => {
      setOpen(updated.subscribable);
      qc.invalidateQueries({ queryKey: ["subscribable"] });
    },
  });

  const drop = useMutation({
    mutationFn: (childSlug: string) => removeSubscriber(space.slug, childSlug),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["subscribers", space.slug] });
    },
  });

  return (
    <div>
      <label className="flex items-start gap-2 text-sm">
        <input
          type="checkbox"
          checked={isOpen}
          disabled={toggle.isPending}
          onChange={(e) => toggle.mutate(e.target.checked)}
          className="mt-0.5"
        />
        <span className="min-w-0">
          <span className="block text-xs font-medium">
            Let other spaces inherit this one
          </span>
          <span
            className="block text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            Anyone who can read a subscribing space will be able to read this
            space's items. Turning it off stops new subscriptions; the ones
            below stay until you remove them.
          </span>
        </span>
      </label>

      {toggle.error && (
        <p className="mt-1 text-xs text-red-600" role="alert">
          {toggle.error.message}
        </p>
      )}

      {(subscribers.data?.length ?? 0) > 0 && (
        <>
          <p className="mb-1 mt-2 text-xs font-medium">Inherited by</p>
          <ul className="flex flex-col gap-1">
            {subscribers.data?.map((s) => (
              <li key={s.slug} className="flex items-center gap-2 text-sm">
                <span className="min-w-0 flex-1 truncate">
                  {s.name}
                  <span
                    className="ml-2 text-xs"
                    style={{ color: "var(--color-text-muted)" }}
                  >
                    {s.slug}
                  </span>
                </span>
                <button
                  onClick={() => drop.mutate(s.slug)}
                  disabled={drop.isPending}
                  aria-label={`Revoke ${s.name}`}
                  className="shrink-0 rounded border px-2 py-0.5 text-xs hover:opacity-80 disabled:opacity-50"
                  style={{ borderColor: "var(--color-border)" }}
                >
                  Revoke
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
