import asyncio, csv
from crawl4ai import AsyncWebCrawler, DefaultMarkdownGenerator
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
from crawl4ai.deep_crawling import BestFirstCrawlingStrategy
from crawl4ai.deep_crawling.scorers import KeywordRelevanceScorer
from config import(
    AUTHORS_CSV,
    SCRAPED_SITES_DIR
)

async def crawl_authors():
    urls = []
    try:
        with open(AUTHORS_CSV, 'r') as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                if row and row[0].strip():
                    urls.append(row[0].strip())
    except FileNotFoundError:
        print(f"Error: CSV file not found")
    print(f"{len(urls)} urls loaded.")  

    n = 0
    browser_config = BrowserConfig()
    async with AsyncWebCrawler(config=browser_config) as crawler:
        for link in urls:
            # Configurations
            keyword_scorer = KeywordRelevanceScorer( 
                    keywords=["contact","email"],
                    weight=0.7
            )
            run_config = CrawlerRunConfig(
                    deep_crawl_strategy=BestFirstCrawlingStrategy(
                        max_depth=2,
                        include_external=False,
                        url_scorer=keyword_scorer,
                        max_pages=8
                    ),
                    markdown_generator = DefaultMarkdownGenerator()
            )

            output_file = f'{n}.md'
            combined_md = ""
            try:
                results = await crawler.arun(link, config=run_config)
                print(link, "results:", type(results), "len:", len(results))
                for result in results:    
                    if result.success:
                        combined_md += f"\n\n{result.markdown}"
                        print("Found subpage.")
                    else:
                        print("ERROR:", result.error_message)
                        continue
                try:
                    with open(SCRAPED_SITES_DIR / output_file, "w", encoding="utf-8") as f:
                        f.write(combined_md)
                        print(f"Successfully saved {link} to {output_file}")
                        n=n+1
                except OSError as e:
                    print(f"Failed to write file: {e}")
            except Exception as e:
                print("Error during crawler.arun", repr(e))

    
                
async def main():
    crawl_authors()


if __name__ == "__main__":
    asyncio.run(main())
