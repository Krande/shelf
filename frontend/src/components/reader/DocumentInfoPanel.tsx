/**
 * The reader's right-hand drawer with the open document's details —
 * the library's own ItemDetail panel, so metadata, revisions, tags,
 * attachments and notes read and edit the same way in both places.
 *
 * The reader is addressed by attachment id alone, so this resolves
 * attachment → item → the space the item lives in, and takes the
 * caller's role there as the answer to "can this be edited", the same
 * rule the library and the API apply.
 */

import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { getAttachment } from "@/api/attachments";
import { listCollections, setItemCollections } from "@/api/collections";
import type { ItemType } from "@/api/itemFields";
import { deleteItem, getItem, updateItem } from "@/api/items";
import { canEdit, fetchMySpaces } from "@/api/spaces";
import { listTags, resolveTagNames, setItemTags, type Tag } from "@/api/tags";
import ItemDetail from "@/components/library/ItemDetail";
import ItemForm, {
  type ItemFormSubmission,
} from "@/components/library/ItemForm";

export function DocumentInfoPanel({
  attachmentId,
  onClose,
}: {
  attachmentId: string;
  onClose: () => void;
}) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);

  const attachment = useQuery({
    queryKey: ["attachment", attachmentId],
    queryFn: () => getAttachment(attachmentId),
  });
  const itemId = attachment.data?.item_id;
  // Same key the library uses for a deep-linked item, so an edit made
  // here is what the library shows when the reader goes back to it.
  const item = useQuery({
    queryKey: ["item", itemId],
    queryFn: () => getItem(itemId!),
    enabled: !!itemId,
  });
  const spaces = useQuery({
    queryKey: ["spaces", "with-inherited"],
    queryFn: () => fetchMySpaces({ includeInherited: true }),
  });
  const space = useMemo(
    () => spaces.data?.find((s) => s.id === item.data?.space_id),
    [spaces.data, item.data?.space_id],
  );
  const slug = space?.slug ?? null;

  const collections = useQuery({
    queryKey: ["collections", slug],
    queryFn: () => listCollections(slug!),
    enabled: !!slug,
  });
  const tags = useQuery({
    queryKey: ["tags", slug],
    queryFn: () => listTags(slug!),
    enabled: !!slug,
  });
  const tagNames = useMemo(() => {
    const byId = new Map<string, Tag>();
    for (const t of tags.data ?? []) byId.set(t.id, t);
    return (item.data?.tag_ids ?? [])
      .map((id) => byId.get(id)?.name)
      .filter((n): n is string => typeof n === "string");
  }, [tags.data, item.data?.tag_ids]);

  const save = useMutation({
    mutationFn: async (payload: ItemFormSubmission) => {
      const id = item.data!.id;
      await updateItem(id, { item_type: payload.item_type, data: payload.data });
      await setItemCollections(id, payload.collection_ids);
      const { ids } = await resolveTagNames(slug!, payload.tag_names, tags.data);
      await setItemTags(id, ids);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["item", itemId] });
      qc.invalidateQueries({ queryKey: ["items", slug] });
      qc.invalidateQueries({ queryKey: ["tags", slug] });
      setEditing(false);
    },
  });

  const trash = useMutation({
    mutationFn: (id: string) => deleteItem(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["items", slug] });
      // The document is in the trash now; reading on would be reading
      // something the library no longer lists.
      nav(slug ? `/library?space=${encodeURIComponent(slug)}` : "/library");
    },
  });

  // Links out of the panel land in the library of the space the item
  // lives in, which is where its tags and collections mean something.
  const toLibrary = (params: Record<string, string>) => {
    const qs = new URLSearchParams(slug ? { space: slug, ...params } : params);
    nav(`/library?${qs.toString()}`);
  };

  const loading = attachment.isPending || item.isPending || spaces.isPending;
  const failed = attachment.error ?? item.error;

  return (
    <aside
      aria-label="Document info"
      // The library's detail-panel widths, so the same panel lays out
      // the same way in both.
      className="flex w-full flex-col border-l sm:w-[360px] lg:w-[420px]"
      style={{
        borderColor: "var(--color-border)",
        backgroundColor: "var(--color-surface)",
      }}
    >
      {failed ? (
        <p className="p-4 text-sm text-red-500">
          Couldn't load this document's details: {(failed as Error).message}
        </p>
      ) : loading || !item.data ? (
        <div className="flex flex-1 items-center justify-center">
          <Loader2
            className="h-5 w-5 animate-spin"
            style={{ color: "var(--color-text-muted)" }}
          />
        </div>
      ) : (
        <div className="min-h-0 flex-1">
          <ItemDetail
            item={item.data}
            collections={collections.data ?? []}
            tagNames={tagNames}
            spaceSlug={slug}
            spaceIsOwned={space?.is_owner ?? false}
            canWrite={canEdit(space)}
            onEdit={() => setEditing(true)}
            onDelete={() => {
              const title = item.data.data.title || "(untitled)";
              if (window.confirm(`Move "${title}" to trash?`)) {
                trash.mutate(item.data.id);
              }
            }}
            onClose={onClose}
            onTagClick={(tag) => toLibrary({ tag })}
            onCollectionClick={(collection) => toLibrary({ collection })}
            onSelectItem={(id) => toLibrary({ item: id })}
          />
        </div>
      )}

      {item.data && (
        <ItemForm
          open={editing}
          title="Edit Item"
          // Modeless: the PDF behind it is usually where the values
          // being typed in come from.
          modal={false}
          slug={slug}
          initial={{
            item_type: item.data.item_type as ItemType,
            data: item.data.data,
            collection_ids: item.data.collection_ids,
            tag_names: tagNames,
          }}
          loading={save.isPending}
          onSubmit={(payload) => save.mutate(payload)}
          onClose={() => setEditing(false)}
        />
      )}
    </aside>
  );
}
