"use client";

import { StreamdownTextPrimitive } from "@assistant-ui/react-streamdown";
import { code } from "@streamdown/code";
import { memo, type ComponentProps, type FC } from "react";

import { cn } from "@/lib/utils";
import { markdownRemarkPlugins } from "@/chat/markdown-plugins";

const MarkdownTextImpl: FC = () => {
  return (
    <StreamdownTextPrimitive
      containerClassName="aui-md"
      components={defaultComponents}
      plugins={{ code }}
      remarkPlugins={markdownRemarkPlugins}
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
    <span className={cn("aui-md-a text-foreground/90", className)}>{children}</span>
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
    <thead className={cn("aui-md-thead bg-transparent", className)} {...props} />
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
      className={cn("aui-md-tr m-0 p-0 [&:last-child>td]:border-b-0", className)}
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
