"use client";

import { StreamdownTextPrimitive } from "@assistant-ui/react-streamdown";
import { code } from "@streamdown/code";
import { memo, useMemo, type ComponentProps, type FC } from "react";
import {
  InlineCitation,
  type Citation,
} from "@/components/ai-elements/inline-citation";
import {
  LedgerCitationPreview,
  useLedgerCitations,
  type LedgerCitation,
} from "@/components/ai-elements/ledger-citation";
import { useSources } from "@/components/ai-elements/sources";

import { cn } from "@/lib/utils";
import { markdownRemarkPlugins } from "@/chat/markdown-plugins";

type CitationNode = {
  type: string;
  value?: string;
  url?: string;
  children?: CitationNode[];
};

function citationPlugin(sources: Citation[], ledger: LedgerCitation[]) {
  return () => (tree: CitationNode) => {
    const visit = (node: CitationNode) => {
      if (!node.children || node.type === "link") return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== "text" || !child.value) {
          visit(child);
          return [child];
        }
        const pieces = child.value.split(/(\[\d+\]|\[movement:mov_[\w-]+\])/g);
        return pieces.filter(Boolean).map((piece) => {
          const source =
            sources.find((item) => piece === `[${item.id}]`) ??
            ledger.find((item) => piece === `[movement:${item.id}]`);
          return source
            ? {
                type: "link",
                url:
                  "amount" in source
                    ? source.url
                    : `https://arno.invalid/source/${encodeURIComponent(source.id)}`,
                children: [{ type: "text", value: piece }],
              }
            : { type: "text", value: piece };
        });
      });
    };
    visit(tree);
  };
}

const MarkdownTextImpl: FC = () => {
  const sources = useSources();
  const ledger = useLedgerCitations();
  const plugins = useMemo(
    () => [...markdownRemarkPlugins, citationPlugin(sources, ledger)],
    [sources, ledger],
  );
  const components = useMemo(
    () => ({
      ...defaultComponents,
      a: ({ href, children, className }: ComponentProps<"a">) => {
        const movement = ledger.find((item) => item.url === href);
        if (movement) return <LedgerCitationPreview source={movement} />;
        const source = sources.find(
          (item) =>
            `https://arno.invalid/source/${encodeURIComponent(item.id)}` ===
            href,
        );
        return source ? (
          <InlineCitation source={source}>{children}</InlineCitation>
        ) : (
          <span className={className}>{children}</span>
        );
      },
    }),
    [sources, ledger],
  );
  return (
    <StreamdownTextPrimitive
      containerClassName="aui-md"
      components={components}
      plugins={{ code }}
      remarkPlugins={plugins}
      controls={{ code: true, table: false }}
      codeBlockMaxHeight={320}
      // Finance replies may quote untrusted text. The custom link and image
      // renderers below never create a live link or load a remote image, and
      // this hardening keeps raw HTML and non-web protocols out of the tree.
      security={{
        allowedProtocols: ["https", "http", "mailto"],
        allowDataImages: false,
      }}
      remarkRehypeOptions={{ allowDangerousHtml: false }}
      defer
    />
  );
};

export const MarkdownText = memo(MarkdownTextImpl);

const defaultComponents = {
  h1: ({ className, ...props }: ComponentProps<"h1">) => (
    <h1
      className={cn(
        "aui-md-h1 mt-5 mb-2 scroll-m-20 text-xl font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h2: ({ className, ...props }: ComponentProps<"h2">) => (
    <h2
      className={cn(
        "aui-md-h2 mt-5 mb-2 scroll-m-20 text-lg font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h3: ({ className, ...props }: ComponentProps<"h3">) => (
    <h3
      className={cn(
        "aui-md-h3 mt-4 mb-1.5 scroll-m-20 text-base font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h4: ({ className, ...props }: ComponentProps<"h4">) => (
    <h4
      className={cn(
        "aui-md-h4 mt-3.5 mb-1 scroll-m-20 text-base font-medium first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h5: ({ className, ...props }: ComponentProps<"h5">) => (
    <h5
      className={cn(
        "aui-md-h5 mt-3 mb-1 text-sm font-semibold first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  h6: ({ className, ...props }: ComponentProps<"h6">) => (
    <h6
      className={cn(
        "aui-md-h6 mt-3 mb-1 text-sm font-medium first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  p: ({ className, ...props }: ComponentProps<"p">) => (
    <p
      className={cn(
        "aui-md-p my-3 leading-relaxed first:mt-0 last:mb-0",
        className,
      )}
      {...props}
    />
  ),
  // Finance replies may quote untrusted text. Never turn it into a live link,
  // and never let a remote image load by itself.
  a: ({ className, children }: ComponentProps<"a">) => (
    <span className={cn("aui-md-a text-foreground/90", className)}>
      {children}
    </span>
  ),
  img: () => null,
  blockquote: ({ className, ...props }: ComponentProps<"blockquote">) => (
    <blockquote
      className={cn(
        "aui-md-blockquote border-muted-foreground/30 text-muted-foreground my-3 border-s-2 ps-4",
        className,
      )}
      {...props}
    />
  ),
  ul: ({ className, ...props }: ComponentProps<"ul">) => (
    <ul
      className={cn(
        "aui-md-ul marker:text-muted-foreground my-3 ms-5 list-disc [&>li]:mt-1",
        className,
      )}
      {...props}
    />
  ),
  ol: ({ className, ...props }: ComponentProps<"ol">) => (
    <ol
      className={cn(
        "aui-md-ol marker:text-muted-foreground my-3 ms-5 list-decimal [&>li]:mt-1",
        className,
      )}
      {...props}
    />
  ),
  hr: ({ className, ...props }: ComponentProps<"hr">) => (
    <hr
      className={cn("aui-md-hr border-muted-foreground/20 my-3", className)}
      {...props}
    />
  ),
  table: ({ className, ...props }: ComponentProps<"table">) => (
    <div className="aui-md-table-wrapper answer-md-table my-3 overflow-x-auto">
      <table
        className={cn(
          "aui-md-table w-full border-collapse text-[13px]",
          className,
        )}
        {...props}
      />
    </div>
  ),
  thead: ({ className, ...props }: ComponentProps<"thead">) => (
    <thead
      className={cn("aui-md-thead bg-transparent", className)}
      {...props}
    />
  ),
  th: ({ className, ...props }: ComponentProps<"th">) => (
    <th
      className={cn(
        "aui-md-th border-muted-foreground/20 border-b px-0 py-2 pe-3 text-start text-[11px] font-medium text-muted-foreground last:pe-0 [[align=center]]:text-center [[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  td: ({ className, ...props }: ComponentProps<"td">) => (
    <td
      className={cn(
        "aui-md-td border-muted-foreground/20 border-b px-0 py-2 pe-3 text-start align-top last:pe-0 [[align=center]]:text-center [[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  tr: ({ className, ...props }: ComponentProps<"tr">) => (
    <tr
      className={cn(
        "aui-md-tr m-0 p-0 [&:last-child>td]:border-b-0",
        className,
      )}
      {...props}
    />
  ),
  li: ({ className, ...props }: ComponentProps<"li">) => (
    <li className={cn("aui-md-li leading-relaxed", className)} {...props} />
  ),
  strong: ({ className, ...props }: ComponentProps<"strong">) => (
    <strong
      className={cn("aui-md-strong font-semibold", className)}
      {...props}
    />
  ),
  sup: ({ className, ...props }: ComponentProps<"sup">) => (
    <sup
      className={cn("aui-md-sup [&>a]:text-xs [&>a]:no-underline", className)}
      {...props}
    />
  ),
  inlineCode: ({ className, ...props }: ComponentProps<"code">) => (
    <code
      className={cn(
        "aui-md-inline-code bg-muted rounded-md px-1.5 py-0.5 font-mono text-[0.85em]",
        className,
      )}
      {...props}
    />
  ),
};
