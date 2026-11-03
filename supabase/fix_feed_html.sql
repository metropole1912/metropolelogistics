-- One-off cleanup: remove HTML code that Google News put into event summaries,
-- and move the " - Publisher" suffix from titles into the summary.
-- Paste into Supabase > SQL Editor and click Run. Safe to run more than once.

update events
set summary = case
      when title ~ '\s-\s[^-]{2,80}$' then 'Reported by ' || trim(substring(title from '\s-\s([^-]{2,80})$')) || '.'
      else ''
    end,
    title = regexp_replace(title, '\s-\s[^-]{2,80}$', '')
where summary ~ '<|href=|&nbsp;'
   or (source_urls[1] like '%news.google.com%' and title ~ '\s-\s[^-]{2,80}$');

update raw_items
set body = ''
where body ~ '<|href=|&nbsp;';

select count(*) as events_still_with_html from events where summary ~ '<|href=';
